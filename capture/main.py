"""
OrderForge Capture — Bitfinex R0 Raw Books (L3) primary source.
Replaces capture/main.py (Coinbase stub).

Line format (unchanged contract):
    <local_ts_ns>|<channel>|<raw_json>

Bitfinex-specific channels written:
    bf_snapshot   — initial full book snapshot (list of [ORDER_ID, PRICE, AMOUNT])
    bf_update     — single order update     [ORDER_ID, PRICE, AMOUNT]
    bf_heartbeat  — exchange heartbeat      {"hb": true}
    bf_info       — connection info/version messages

S3 upload:
    Triggered at UTC midnight file rotation and every 10 minutes for snapshots.
    Requires IAM role on EC2 with s3:PutObject on your bucket.
    Set env var: S3_BUCKET=your-bucket-name
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import websockets

from common.time_utils import now_local_ns

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BITFINEX_WS_URL = "wss://api-pub.bitfinex.com/ws/2"
SYMBOL = "tBTCUSD"          # Bitfinex pair notation
SNAPSHOT_INTERVAL_S = 600   # 10 minutes
S3_BUCKET = os.getenv("S3_BUCKET", "")          # e.g. "orderforge-capture"
S3_PREFIX = os.getenv("S3_PREFIX", "bitfinex/btcusd")  # key prefix inside bucket


# ── Line builder (preserves existing contract) ────────────────────────────────
def build_line(channel: str, raw_json: str) -> str:
    """Returns: <local_ts_ns>|<channel>|<raw_json>"""
    return f"{now_local_ns()}|{channel}|{raw_json}"


# ── File path helpers ─────────────────────────────────────────────────────────
def event_log_path(base_dir: Path, utc_date: str) -> Path:
    """Incremental L3 event log: data/YYYY-MM-DD/events.log"""
    p = base_dir / utc_date
    p.mkdir(parents=True, exist_ok=True)
    return p / "events.log"


def snapshot_path(base_dir: Path, utc_date: str, utc_time: str) -> Path:
    """Periodic snapshot: data/YYYY-MM-DD/snapshot_HHMMSS.log"""
    p = base_dir / utc_date
    p.mkdir(parents=True, exist_ok=True)
    return p / f"snapshot_{utc_time}.log"


# ── S3 upload ─────────────────────────────────────────────────────────────────
def upload_to_s3(local_path: Path, s3_key: str) -> None:
    """Best-effort S3 upload. Logs errors but never raises."""
    if not S3_BUCKET:
        return
    try:
        s3 = boto3.client("s3")
        s3.upload_file(str(local_path), S3_BUCKET, s3_key)
        log.info(f"Uploaded {local_path} -> s3://{S3_BUCKET}/{s3_key}")
    except Exception as exc:
        log.error(f"S3 upload failed for {local_path}: {exc}")


def make_s3_key(filename: str, utc_date: str) -> str:
    return f"{S3_PREFIX}/{utc_date}/{filename}"


# ── Bitfinex subscription helpers ─────────────────────────────────────────────
SUBSCRIBE_MSG = json.dumps({
    "event": "subscribe",
    "channel": "book",
    "prec": "R0",       # Raw / L3 — individual order IDs, not price levels
    "freq": "F0",       # Real-time (not batched)
    "symbol": SYMBOL,
    "len": "100",       # 100 orders per side in snapshot
})

CHECKSUM_MSG = json.dumps({
    "event": "conf",
    "flags": 131072,    # Enable CRC32 checksums
})


def classify_bitfinex_msg(raw: str) -> tuple[str, str]:
    """
    Parse a raw Bitfinex WebSocket message and return (channel_label, raw_json).

    Bitfinex message types:
      {"event": "info", ...}          — connection info
      {"event": "subscribed", ...}    — subscription confirmation → gives us CHAN_ID
      {"event": "pong", ...}          — heartbeat response
      [CHAN_ID, "hb"]                 — channel heartbeat
      [CHAN_ID, [[OID,P,A],...]]      — snapshot (nested list)
      [CHAN_ID, OID, P, A]            — single update (4-element array)
      [CHAN_ID, "cs", CHECKSUM]       — checksum frame
    """
    data = json.loads(raw)

    if isinstance(data, dict):
        event = data.get("event", "info")
        return f"bf_{event}", raw

    if isinstance(data, list):
        if len(data) == 2:
            payload = data[1]
            if payload == "hb":
                return "bf_heartbeat", raw
            if isinstance(payload, str):
                return "bf_checksum", raw
            if isinstance(payload, list):
                # Single update: [CHAN_ID, [OID, PRICE, AMOUNT]]
                if len(payload) == 3 and not isinstance(payload[0], list):
                    return "bf_update", raw
                # Full snapshot: [CHAN_ID, [[OID,P,A], [OID,P,A], ...]]
                if len(payload) > 3 or isinstance(payload[0], list):
                    return "bf_snapshot", raw
        if len(data) == 4:
            return "bf_update", raw
        if len(data) == 3 and data[1] == "cs":
            return "bf_checksum", raw

    return "bf_unknown", raw


# ── Writer coroutine ──────────────────────────────────────────────────────────
async def file_writer(
    queue: asyncio.Queue,
    base_dir: Path,
    snapshot_queue: asyncio.Queue,
) -> None:
    """
    Reads (channel, raw_json) tuples from queue.
    Writes to daily event log, rotates at UTC midnight.
    Signals snapshot_queue when it's time for a snapshot.
    """
    utc_now = datetime.now(timezone.utc)
    current_date = utc_now.strftime("%Y-%m-%d")
    current_file_path = event_log_path(base_dir, current_date)
    file_handle = current_file_path.open("a", encoding="utf-8")
    log.info(f"Writer opened event log: {current_file_path}")

    last_snapshot_ts = asyncio.get_event_loop().time()

    try:
        while True:
            try:
                channel, raw_json = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Check for rotation / snapshot even when queue is idle
                channel, raw_json = None, None

            now_utc = datetime.now(timezone.utc)
            now_date = now_utc.strftime("%Y-%m-%d")

            # ── UTC midnight rotation ──────────────────────────────────────────
            if now_date != current_date:
                file_handle.close()
                # Upload rotated file to S3
                s3_key = make_s3_key("events.log", current_date)
                asyncio.get_event_loop().run_in_executor(
                    None, upload_to_s3, current_file_path, s3_key
                )
                current_date = now_date
                current_file_path = event_log_path(base_dir, current_date)
                file_handle = current_file_path.open("a", encoding="utf-8")
                log.info(f"Rotated to new event log: {current_file_path}")

            # ── Write the line ─────────────────────────────────────────────────
            if channel and raw_json:
                line = build_line(channel, raw_json)
                file_handle.write(line + "\n")
                file_handle.flush()
                queue.task_done()

            # ── 10-minute snapshot trigger ─────────────────────────────────────
            now_mono = asyncio.get_event_loop().time()
            if now_mono - last_snapshot_ts >= SNAPSHOT_INTERVAL_S:
                last_snapshot_ts = now_mono
                snap_time = now_utc.strftime("%H%M%S")
                await snapshot_queue.put((current_date, snap_time))

    except asyncio.CancelledError:
        log.info("Writer cancelled — flushing and closing.")
        file_handle.flush()
        file_handle.close()


# ── Snapshot writer ───────────────────────────────────────────────────────────
async def snapshot_writer(
    snapshot_queue: asyncio.Queue,
    book_state: dict,
    base_dir: Path,
) -> None:
    """
    Listens for snapshot triggers and dumps current in-memory book state to disk.
    book_state is populated and maintained by the listener.
    """
    while True:
        try:
            utc_date, utc_time = await snapshot_queue.get()
            snap = json.dumps(book_state, separators=(",", ":"))
            snap_path = snapshot_path(base_dir, utc_date, utc_time)
            with snap_path.open("w", encoding="utf-8") as f:
                f.write(snap + "\n")
            log.info(f"Snapshot written: {snap_path} ({len(book_state)} orders)")

            # Upload snapshot to S3
            s3_key = make_s3_key(f"snapshot_{utc_time}.log", utc_date)
            upload_to_s3(snap_path, s3_key)

            snapshot_queue.task_done()
        except asyncio.CancelledError:
            break


# ── In-memory order book maintenance ──────────────────────────────────────────
def apply_snapshot(book_state: dict, orders: list) -> None:
    book_state.clear()
    for entry in orders:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        order_id, price, amount = entry[0], entry[1], entry[2]
        book_state[str(order_id)] = {"price": price, "amount": amount}

def apply_update(book_state: dict, order_id: int, price: float, amount: float) -> None:
    key = str(order_id)
    if price == 0:
        book_state.pop(key, None)
    else:
        book_state[key] = {"price": price, "amount": amount}
# ── WebSocket listener ────────────────────────────────────────────────────────
async def websocket_listener(
    queue: asyncio.Queue,
    book_state: dict,
) -> None:

    """
    Connects to Bitfinex, subscribes to R0 Raw Books for tBTCUSD,
    maintains in-memory book_state, and puts raw messages on queue.
    Reconnects indefinitely on any failure.
    """
    backoff = 5

    while True:
        try:
            log.info(f"Connecting to {BITFINEX_WS_URL} ...")
            async with websockets.connect(
                BITFINEX_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                max_size=10 * 1024 * 1024,  # 10 MB — snapshots can be large
            ) as ws:
                log.info("Connected. Enabling checksums and subscribing ...")
                await ws.send(CHECKSUM_MSG)
                await ws.send(SUBSCRIBE_MSG)
                backoff = 5  # reset on successful connect

                async for raw_msg in ws:
                    channel_label, raw_json = classify_bitfinex_msg(raw_msg)

                    # Maintain in-memory book for snapshots
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, list):
                            if len(data) == 2 and isinstance(data[1], list):
                                # Snapshot
                                apply_snapshot(book_state, data[1])
                            elif len(data) == 4 and not isinstance(data[1], str):
                                # Update: [chan_id, order_id, price, amount]
                                apply_update(book_state, data[1], data[2], data[3])
                    except Exception as e:
                        log.warning(f"Book state update error: {e}")

                    # Skip pure heartbeats and checksums from disk (save I/O)
                    if channel_label in ("bf_heartbeat", "bf_checksum", "bf_pong"):
                        continue

                    await queue.put((channel_label, raw_msg))

        except websockets.ConnectionClosed as e:
            log.warning(f"Connection closed ({e}). Reconnecting in {backoff}s ...")
        except Exception as e:
            log.error(f"Listener error: {e}. Reconnecting in {backoff}s ...")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)  # exponential backoff, cap at 60s


# ── Main orchestrator ─────────────────────────────────────────────────────────
async def async_main(base_dir: Path) -> None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    snapshot_queue: asyncio.Queue = asyncio.Queue()
    book_state: dict = {}  # shared mutable state: {order_id_str: {price, amount}}

    listener = asyncio.create_task(
        websocket_listener(queue, book_state), name="listener"
    )
    writer = asyncio.create_task(
        file_writer(queue, base_dir, snapshot_queue), name="writer"
    )
    snapshotter = asyncio.create_task(
        snapshot_writer(snapshot_queue, book_state, base_dir), name="snapshotter"
    )

    log.info("OrderForge Bitfinex capture running. Ctrl-C to stop.")
    try:
        await asyncio.gather(listener, writer, snapshotter)
    except asyncio.CancelledError:
        log.info("Shutdown signal received.")
    finally:
        for task in (listener, writer, snapshotter):
            task.cancel()
        await asyncio.gather(listener, writer, snapshotter, return_exceptions=True)
        log.info("All tasks stopped cleanly.")


def main() -> int:
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    base_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Output base dir: {base_dir.resolve()}")

    if S3_BUCKET:
        log.info(f"S3 uploads enabled: s3://{S3_BUCKET}/{S3_PREFIX}/")
    else:
        log.info("S3_BUCKET not set — running in local-only mode.")

    try:
        asyncio.run(async_main(base_dir))
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())