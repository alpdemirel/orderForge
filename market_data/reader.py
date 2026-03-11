"""
market_data/reader.py — Bitfinex R0 Raw Books L3 format.

Bitfinex channel labels written by capture/main.py:
    bf_snapshot   — initial book snapshot
    bf_update     — single order add/remove
    bf_subscribed — subscription confirmation
    bf_info       — connection info

Line format (dual-timestamp, EC2 colocated with exchange):
    <local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
  - exchange_ts_ns: stamped by exchange at event time (0 if not provided)
  - local_ts_ns: stamped by us at capture time

Legacy format (3 fields) is supported for backwards compatibility:
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
    Parse a capture line. Supports:
      - New (4 fields): <local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
      - Legacy (3 fields): <local_ts_ns>|<channel>|<raw_json> (exchange_ts_ns=0)

    Returns a MarketEvent or None (permissive mode) / raises (strict mode).
    """
    line = line.strip()
    if not line:
        return None

    try:
        parts = line.split("|", 3)
        if len(parts) < 3:
            raise ValueError(f"Expected at least 3 pipe-delimited parts, got {len(parts)}")

        # Detect format: 4 parts and 2nd part is numeric -> new format
        if len(parts) == 4 and parts[1].lstrip("-").isdigit():
            local_ts_ns = int(parts[0])
            exchange_ts_ns = int(parts[1])
            channel = parts[2]
            raw_json = json.loads(parts[3])
        else:
            local_ts_ns = int(parts[0])
            channel = parts[1]
            raw_json = json.loads(parts[2])
            exchange_ts_ns = 0

        return MarketEvent(
            local_ts_ns=local_ts_ns,
            channel=channel,
            raw_json=raw_json,
            exchange_ts_ns=exchange_ts_ns,
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

    Bitfinex sends either:
      [CHAN_ID, ORDER_ID, PRICE, AMOUNT]  (4 elements)
      [CHAN_ID, [ORDER_ID, PRICE, AMOUNT]] (2 elements, payload is 3-element list)
    PRICE == 0 → order removed (cancelled or fully filled).
    """
    raw = event.raw_json
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    payload = raw[1]
    if isinstance(payload, list) and len(payload) == 3:
        order_id, price, amount = payload[0], payload[1], payload[2]
    elif len(raw) >= 4:
        order_id, price, amount = raw[1], raw[2], raw[3]
    else:
        return None
    removed = (price == 0)
    return {
        "order_id": int(order_id),
        "price": float(price),
        "amount": float(amount),
        "side": ("bid" if amount > 0 else "ask") if not removed else None,
        "removed": removed,
    }