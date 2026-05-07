"""Deterministic in-memory order book with sorted price levels."""

from __future__ import annotations

from typing import Callable, Optional

from sortedcontainers import SortedDict

from exchange.models import Order, Side


class OrderBook:
    """Price-time priority order book with O(1) best bid/ask and O(log n) add/cancel."""

    def __init__(self) -> None:
        self._bids: SortedDict[int, dict[str, None]] = SortedDict()
        self._asks: SortedDict[int, dict[str, None]] = SortedDict()
        self._orders: dict[str, Order] = {}
        # Monotonic counter stamped onto every order via add() — used for
        # queue-position modeling (lower seq = arrived earlier = ahead).
        self._next_seq: int = 0

    @property
    def orders(self) -> dict[str, Order]:
        return self._orders

    def add(self, order: Order) -> None:
        if order.order_id in self._orders:
            raise ValueError(f"Order already exists: {order.order_id}")

        order.book_seq = self._next_seq
        self._next_seq += 1
        self._orders[order.order_id] = order
        if order.side == Side.BUY:
            if order.price not in self._bids:
                self._bids[order.price] = {}
            self._bids[order.price][order.order_id] = None
        else:
            if order.price not in self._asks:
                self._asks[order.price] = {}
            self._asks[order.price][order.order_id] = None

    def cancel(self, order_id: str) -> Order | None:
        order = self._orders.pop(order_id, None)
        if order is None:
            return None

        levels = self._bids if order.side == Side.BUY else self._asks
        queue = levels.get(order.price)
        if queue is not None:
            queue.pop(order_id, None)
            if not queue:
                del levels[order.price]

        return order

    def best_bid(self) -> int | None:
        if not self._bids:
            return None
        # SortedDict keeps ascending order, so the best bid is the last key
        return self._bids.keys()[-1]

    def best_ask(self) -> int | None:
        if not self._asks:
            return None
        # SortedDict keeps ascending order, so the best ask is the first key
        return self._asks.keys()[0]

    def pop_best(self, side: Side) -> Order | None:
        levels = self._bids if side == Side.BUY else self._asks
        if not levels:
            return None

        # For bids, best is last; for asks, best is first
        price = self._bids.keys()[-1] if side == Side.BUY else self._asks.keys()[0]
        
        queue = levels[price]
        order_id = next(iter(queue))
        del queue[order_id]
        if not queue:
            del levels[price]
            
        return self._orders.pop(order_id, None)

    def pop_best_filtered(
        self, side: Side, predicate: Callable[[Order], bool],
    ) -> Optional[Order]:
        """Pop best-priced order satisfying *predicate* (price-time priority)."""
        levels = self._bids if side == Side.BUY else self._asks
        if not levels:
            return None

        # Iterate in priority order: bids descending, asks ascending
        # sortedcontainers SortedDict supports fast iteration
        price_iter = reversed(levels) if side == Side.BUY else iter(levels)

        for price in list(price_iter):
            queue = levels[price]
            for order_id in list(queue.keys()):
                order = self._orders.get(order_id)
                if order is not None and predicate(order):
                    del queue[order_id]
                    if not queue:
                        del levels[price]
                    return self._orders.pop(order_id)
        return None

    def depth_snapshot(self, levels: int = 5) -> dict:
        """Return top *levels* of the bid and ask sides."""
        def _aggregate(side_levels: SortedDict, descending: bool) -> list[tuple[int, int]]:
            result = []
            price_iter = reversed(side_levels) if descending else iter(side_levels)
            
            for p in price_iter:
                if len(result) >= levels:
                    break
                total = 0
                for oid in side_levels[p]:
                    order = self._orders.get(oid)
                    if order is not None:
                        total += order.remaining
                if total > 0:
                    result.append((p, total))
            return result

        return {
            "bids": _aggregate(self._bids, descending=True),
            "asks": _aggregate(self._asks, descending=False),
        }

    def count_by_owner(self, side: Side, owner_id: str) -> int:
        """Count orders on *side* owned by *owner_id*."""
        levels = self._bids if side == Side.BUY else self._asks
        count = 0
        for queue in levels.values():
            for oid in queue:
                order = self._orders.get(oid)
                if order is not None and order.owner_id == owner_id:
                    count += 1
        return count

    def worst_orders(self, side: Side, owner_id: str, n: int) -> list[str]:
        """Return up to *n* worst-priced order IDs owned by *owner_id*."""
        levels = self._bids if side == Side.BUY else self._asks
        if not levels:
            return []

        # Worst prices: ascending for bids (lowest = worst), descending for asks (highest = worst)
        price_iter = iter(levels) if side == Side.BUY else reversed(levels)

        result: list[str] = []
        for price in price_iter:
            if len(result) >= n:
                break
            queue = levels[price]
            for oid in queue:
                if len(result) >= n:
                    break
                order = self._orders.get(oid)
                if order is not None and order.owner_id == owner_id:
                    result.append(oid)
        return result

    def market_depth_ahead(
        self, side: Side, price: int, owner_id_filter: str, before_seq: int | None = None,
    ) -> int:
        """Total quantity of orders at *price* on *side* owned by *owner_id_filter*."""
        levels = self._bids if side == Side.BUY else self._asks
        queue = levels.get(price)
        if queue is None:
            return 0
        total = 0
        for order_id in queue:
            order = self._orders.get(order_id)
            if order is None or order.owner_id != owner_id_filter:
                continue
            if before_seq is not None and order.book_seq >= before_seq:
                continue
            total += order.remaining
        return total
