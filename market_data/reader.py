"""
market_data/reader.py — updated for Bitfinex R0 Raw Books L3 format.

Bitfinex channel labels written by capture/main.py:
    bf_snapshot   — initial book snapshot
    bf_update     — single order add/remove
    bf_subscribed — subscription confirmation
    bf_info       — connection info

Line format (unchanged project contract):
    <local_ts_ns>|<channel>|<raw_json>

For bf_snapshot:
    raw_json is the full WebSocket frame:
    [CHAN_ID, [[ORDER_ID, PRICE, AMOUNT], ...]]

For bf_update:
    raw_json is:
    [CHAN_ID, ORDER_ID, PRICE, AMOUNT]

MarketEvent.raw_json holds the parsed Python object (list or dict).
Callers must check event.channel to know the structure.
"""

from __future__ import annotations

import json
import logging
from typing import Iterator, Optional

from common.types import MarketEvent
from market_data.exceptions import MalformedRecordError

logger = logging.getLogger(__name__)


def parse_line(line: str, strict: bool = False) -> Optional[MarketEvent]:
    """
    Parse a single capture line: <local_ts_ns>|<channel>|<raw_json>

    Returns a MarketEvent or None (permissive mode) / raises (strict mode).
    """
    line = line.strip()
    if not line:
        return None

    try:
        parts = line.split("|", 2)
        if len(parts) != 3:
            raise ValueError(f"Expected 3 pipe-delimited parts, got {len(parts)}")

        local_ts_ns = int(parts[0])
        channel = parts[1]
        raw_json = json.loads(parts[2])

        return MarketEvent(
            local_ts_ns=local_ts_ns,
            channel=channel,
            raw_json=raw_json,
        )

    except Exception as exc:
        if strict:
            raise MalformedRecordError(f"Failed to parse line: {exc}") from exc
        logger.warning(f"Skipping malformed line: {exc}")
        return None


def read_capture_file(filepath: str, strict: bool = False) -> Iterator[MarketEvent]:
    """
    Yield MarketEvents from a capture event log or snapshot file.
    Skips malformed lines in permissive mode (default).
    """
    with open(filepath, "r", encoding="utf-8") as fh:
        for line in fh:
            event = parse_line(line, strict=strict)
            if event is not None:
                yield event


# ── Bitfinex-specific helpers for downstream consumers ────────────────────────

def is_bitfinex_snapshot(event: MarketEvent) -> bool:
    return event.channel == "bf_snapshot"


def is_bitfinex_update(event: MarketEvent) -> bool:
    return event.channel == "bf_update"


def extract_bitfinex_snapshot_orders(event: MarketEvent) -> list[dict]:
    """
    From a bf_snapshot event, return a list of order dicts:
        [{"order_id": int, "price": float, "amount": float, "side": "bid"|"ask"}, ...]

    raw_json structure: [CHAN_ID, [[ORDER_ID, PRICE, AMOUNT], ...]]
    Positive AMOUNT = bid, negative = ask.
    PRICE == 0 means removal (should not appear in snapshots, but guard anyway).
    """
    raw = event.raw_json
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    orders_raw = raw[1]
    if not isinstance(orders_raw, list):
        return []

    result = []
    for entry in orders_raw:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        order_id, price, amount = entry[0], entry[1], entry[2]
        if price == 0:
            continue
        result.append({
            "order_id": int(order_id),
            "price": float(price),
            "amount": float(amount),
            "side": "bid" if amount > 0 else "ask",
        })
    return result


def extract_bitfinex_update(event: MarketEvent) -> dict | None:
    """
    From a bf_update event, return a single order dict:
        {"order_id": int, "price": float, "amount": float,
         "side": "bid"|"ask"|None, "removed": bool}

    raw_json structure: [CHAN_ID, ORDER_ID, PRICE, AMOUNT]
    PRICE == 0 → order removed (cancelled or fully filled).
    """
    raw = event.raw_json
    if not isinstance(raw, list) or len(raw) < 4:
        return None
    _, order_id, price, amount = raw[0], raw[1], raw[2], raw[3]
    removed = (price == 0)
    return {
        "order_id": int(order_id),
        "price": float(price),
        "amount": float(amount),
        "side": ("bid" if amount > 0 else "ask") if not removed else None,
        "removed": removed,
    }