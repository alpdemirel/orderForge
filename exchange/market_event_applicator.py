"""Bridge between Bitfinex L3 market data and the exchange OrderBook.

Translates MarketEvent objects (from the WebSocket capture stream) and
S3 snapshot dicts into passive resting orders on the book. These market
orders are tagged with owner_id = MARKET_OWNER_ID so they can be
distinguished from strategy orders.

The sim loop calls apply() for each MarketEvent popped from the heap,
and apply_s3_snapshot() once at startup to seed the book from a
snapshot_HHMMSS.log file.
"""

from __future__ import annotations

import logging

from common.types import MarketEvent
from exchange.models import Order, Side, TimeInForce, PRICE_MULTIPLIER, QUANTITY_MULTIPLIER
from exchange.order_book import OrderBook
from market_data.reader import (
    is_bitfinex_snapshot,
    is_bitfinex_update,
    extract_bitfinex_snapshot_orders,
    extract_bitfinex_update,
)

MARKET_OWNER_ID = "__market__"

logger = logging.getLogger(__name__)


class MarketEventApplicator:
    """Maintains the passive (market) side of the LOB from L3 data."""

    def __init__(self, book: OrderBook) -> None:
        self._book = book
        self._bitfinex_id_to_internal_id: dict[int, str] = {}

    # ── Public API ───────────────────────────────────────────────────────

    def apply(self, event: MarketEvent) -> None:
        """Dispatch a MarketEvent from the WebSocket capture stream."""
        if is_bitfinex_snapshot(event):
            self._apply_snapshot(event)
        elif is_bitfinex_update(event):
            self._apply_update(event)
        # all other channels (bf_info, bf_conf, bf_subscribed, heartbeat) → no-op

    def apply_s3_snapshot(self, snapshot_data: dict) -> None:
        """Seed the LOB from an S3 snapshot_HHMMSS.log dict.

        snapshot_data format: {"order_id_str": {"price": number, "amount": float}, ...}
        Positive amount = bid, negative = ask.
        """
        self._clear_market_orders()
        for order_id_str, entry in snapshot_data.items():
            amount = entry["amount"]
            self._insert_market_order(
                bitfinex_id=int(order_id_str),
                float_price=entry["price"],
                float_amount=amount,
                side_str="bid" if amount > 0 else "ask",
            )

    # ── Internal handlers ────────────────────────────────────────────────

    def _apply_snapshot(self, event: MarketEvent) -> None:
        self._clear_market_orders()
        for raw in extract_bitfinex_snapshot_orders(event):
            self._insert_market_order(
                bitfinex_id=raw["order_id"],
                float_price=raw["price"],
                float_amount=raw["amount"],
                side_str=raw["side"],
            )

    def _apply_update(self, event: MarketEvent) -> None:
        raw = extract_bitfinex_update(event)
        if raw is None:
            return

        bitfinex_id: int = raw["order_id"]

        if raw["removed"]:
            self._remove_market_order(bitfinex_id)
            return

        # Modify: cancel old resting order first, then re-add at new price/qty
        if bitfinex_id in self._bitfinex_id_to_internal_id:
            self._remove_market_order(bitfinex_id)

        self._insert_market_order(
            bitfinex_id=bitfinex_id,
            float_price=raw["price"],
            float_amount=raw["amount"],
            side_str=raw["side"],
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _insert_market_order(
        self,
        bitfinex_id: int,
        float_price: float,
        float_amount: float,
        side_str: str,
    ) -> None:
        internal_id = f"mkt-{bitfinex_id}"
        order = Order(
            order_id=internal_id,
            side=Side.BUY if side_str == "bid" else Side.SELL,
            price=round(float_price * PRICE_MULTIPLIER),
            quantity=round(abs(float_amount) * QUANTITY_MULTIPLIER),
            owner_id=MARKET_OWNER_ID,
            tif=TimeInForce.GTC,
        )
        self._book.add(order)
        self._bitfinex_id_to_internal_id[bitfinex_id] = internal_id

    def _remove_market_order(self, bitfinex_id: int) -> None:
        internal_id = self._bitfinex_id_to_internal_id.pop(bitfinex_id, None)
        if internal_id is None:
            logger.debug("remove_market_order: unknown bitfinex_id=%d, ignoring", bitfinex_id)
            return
        self._book.cancel(internal_id)

    def _clear_market_orders(self) -> None:
        ids_to_clear = [
            oid for oid, order in self._book.orders.items()
            if order.owner_id == MARKET_OWNER_ID
        ]
        for oid in ids_to_clear:
            self._book.cancel(oid)
        self._bitfinex_id_to_internal_id.clear()
