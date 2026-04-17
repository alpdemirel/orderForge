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
from typing import TYPE_CHECKING

from common.types import MarketEvent
from exchange.models import (
    Fill, MARKET_OWNER_ID, Order, Side, TimeInForce,
    PRICE_MULTIPLIER, QUANTITY_MULTIPLIER,
)
from exchange.order_book import OrderBook
from market_data.reader import (
    is_bitfinex_snapshot,
    is_bitfinex_update,
    extract_bitfinex_snapshot_orders,
    extract_bitfinex_update,
)

if TYPE_CHECKING:
    from exchange.engine import ExchangeEngine

logger = logging.getLogger(__name__)


class MarketEventApplicator:
    """Maintains the passive (market) side of the LOB from L3 data.

    Bitfinex R0 Raw Books with ``len=100`` tracks at most 100 orders per
    side.  When a new order at a better price is added, the worst-price
    order silently drops off the feed — no removal event is sent.  To
    prevent ghost-order accumulation, we prune each side to
    ``r0_window_size`` after every update.
    """

    def __init__(self, engine: ExchangeEngine, r0_window_size: int = 100) -> None:
        self._engine = engine
        self._book = engine.book
        self._bitfinex_id_to_internal_id: dict[int, str] = {}
        self._internal_id_to_bitfinex_id: dict[str, int] = {}
        self._r0_window_size = r0_window_size
        # Running count of market orders per side (avoids O(n) scan each tick)
        self._market_count = {Side.BUY: 0, Side.SELL: 0}

    # ── Public API ───────────────────────────────────────────────────────

    def apply(self, event: MarketEvent) -> list[Fill]:
        """Dispatch a MarketEvent from the WebSocket capture stream.

        Returns fills produced when an incoming market order crosses a
        resting strategy order.
        """
        if is_bitfinex_snapshot(event):
            return self._apply_snapshot(event)
        elif is_bitfinex_update(event):
            return self._apply_update(event)
        # all other channels (bf_info, bf_conf, bf_subscribed, heartbeat) → no-op
        return []

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

    def _apply_snapshot(self, event: MarketEvent) -> list[Fill]:
        self._clear_market_orders()
        all_fills: list[Fill] = []
        for raw in extract_bitfinex_snapshot_orders(event):
            fills = self._insert_market_order(
                bitfinex_id=raw["order_id"],
                float_price=raw["price"],
                float_amount=raw["amount"],
                side_str=raw["side"],
            )
            if fills:
                all_fills.extend(fills)
        return all_fills

    def _apply_update(self, event: MarketEvent) -> list[Fill]:
        raw = extract_bitfinex_update(event)
        if raw is None:
            return []

        bitfinex_id: int = raw["order_id"]

        if raw["removed"]:
            self._remove_market_order(bitfinex_id)
            return []

        # Modify: cancel old resting order first, then re-add at new price/qty
        if bitfinex_id in self._bitfinex_id_to_internal_id:
            self._remove_market_order(bitfinex_id)

        side = Side.BUY if raw["side"] == "bid" else Side.SELL

        fills = self._insert_market_order(
            bitfinex_id=bitfinex_id,
            float_price=raw["price"],
            float_amount=raw["amount"],
            side_str=raw["side"],
        )

        # Prune stale orders that silently fell off the R0 window
        self._prune_r0_window(side)

        return fills

    # ── Helpers ──────────────────────────────────────────────────────────

    def _insert_market_order(
        self,
        bitfinex_id: int,
        float_price: float,
        float_amount: float,
        side_str: str,
    ) -> list[Fill]:
        side = Side.BUY if side_str == "bid" else Side.SELL
        price = round(float_price * PRICE_MULTIPLIER)
        quantity = round(abs(float_amount) * QUANTITY_MULTIPLIER)

        if price <= 0 or quantity <= 0:
            logger.debug(
                "Skipping market order with invalid price=%d or qty=%d (bf_id=%d)",
                price, quantity, bitfinex_id,
            )
            return []

        # Check if this incoming market order crosses any resting strategy
        # orders.  In live trading the strategy order would have been
        # filled by this aggressor.
        fills, remaining = self._engine.match_incoming_against_strategy(
            incoming_side=side,
            price=price,
            quantity=quantity,
            bitfinex_id=bitfinex_id,
        )

        # Place whatever quantity is left as passive market liquidity
        if remaining > 0:
            internal_id = f"mkt-{bitfinex_id}"
            order = Order(
                order_id=internal_id,
                side=side,
                price=price,
                quantity=remaining,
                owner_id=MARKET_OWNER_ID,
                tif=TimeInForce.GTC,
            )
            self._book.add(order)
            self._bitfinex_id_to_internal_id[bitfinex_id] = internal_id
            self._internal_id_to_bitfinex_id[internal_id] = bitfinex_id
            self._market_count[side] += 1

        return fills

    def _remove_market_order(self, bitfinex_id: int) -> None:
        internal_id = self._bitfinex_id_to_internal_id.pop(bitfinex_id, None)
        if internal_id is None:
            logger.debug("remove_market_order: unknown bitfinex_id=%d, ignoring", bitfinex_id)
            return
        order = self._book.orders.get(internal_id)
        if order is not None:
            self._market_count[order.side] -= 1
        self._book.cancel(internal_id)
        self._internal_id_to_bitfinex_id.pop(internal_id, None)

    def _prune_r0_window(self, side: Side) -> None:
        """Remove worst-priced market orders that exceed the R0 window size.

        Bitfinex R0 ``len=100`` only tracks the 100 best orders per side.
        Orders that fall outside this window are silently dropped from the
        feed.  We mirror this by evicting the worst-priced market orders
        when a side exceeds the window size.
        """
        excess = self._market_count[side] - self._r0_window_size
        if excess <= 0:
            return
        worst = self._book.worst_orders(side, MARKET_OWNER_ID, excess)
        for oid in worst:
            bf_id = self._internal_id_to_bitfinex_id.pop(oid, None)
            if bf_id is not None:
                self._bitfinex_id_to_internal_id.pop(bf_id, None)
            self._book.cancel(oid)
            self._market_count[side] -= 1

    def _clear_market_orders(self) -> None:
        ids_to_clear = [
            oid for oid, order in self._book.orders.items()
            if order.owner_id == MARKET_OWNER_ID
        ]
        for oid in ids_to_clear:
            self._book.cancel(oid)
        self._bitfinex_id_to_internal_id.clear()
        self._internal_id_to_bitfinex_id.clear()
        self._market_count = {Side.BUY: 0, Side.SELL: 0}
