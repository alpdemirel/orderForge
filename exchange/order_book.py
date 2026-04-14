"""Deterministic in-memory order book with sorted price levels."""

from __future__ import annotations

import bisect
from collections import defaultdict, deque
from typing import Callable, Optional

from exchange.models import Order, Side


class OrderBook:
    """Price-time priority order book with O(1) best bid/ask and O(log n) add/cancel."""

    def __init__(self) -> None:
        self._bids: dict[int, deque[str]] = defaultdict(deque)
        self._asks: dict[int, deque[str]] = defaultdict(deque)
        self._orders: dict[str, Order] = {}
        # Sorted price level indices — avoids O(n) min/max on every tick
        self._bid_prices: list[int] = []   # ascending order, best = last
        self._ask_prices: list[int] = []   # ascending order, best = first
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
            is_new_level = order.price not in self._bids
            self._bids[order.price].append(order.order_id)
            if is_new_level:
                bisect.insort(self._bid_prices, order.price)
        else:
            is_new_level = order.price not in self._asks
            self._asks[order.price].append(order.order_id)
            if is_new_level:
                bisect.insort(self._ask_prices, order.price)

    def cancel(self, order_id: str) -> Order | None:
        order = self._orders.pop(order_id, None)
        if order is None:
            return None

        levels = self._bids if order.side == Side.BUY else self._asks
        price_list = self._bid_prices if order.side == Side.BUY else self._ask_prices
        queue = levels.get(order.price)
        if queue is not None:
            self._remove_from_queue(queue, order_id)
            if not queue:
                levels.pop(order.price, None)
                self._remove_price(price_list, order.price)

        return order

    def best_bid(self) -> int | None:
        return self._bid_prices[-1] if self._bid_prices else None

    def best_ask(self) -> int | None:
        return self._ask_prices[0] if self._ask_prices else None

    def pop_best(self, side: Side) -> Order | None:
        price = self.best_bid() if side == Side.BUY else self.best_ask()
        if price is None:
            return None

        levels = self._bids if side == Side.BUY else self._asks
        price_list = self._bid_prices if side == Side.BUY else self._ask_prices
        queue = levels[price]
        order_id = queue.popleft()
        if not queue:
            levels.pop(price, None)
            self._remove_price(price_list, price)
        return self._orders.pop(order_id, None)

    def pop_best_filtered(
        self, side: Side, predicate: Callable[[Order], bool],
    ) -> Optional[Order]:
        """Pop best-priced order satisfying *predicate* (price-time priority).

        Scans price levels in priority order (highest bid / lowest ask) and
        within each level scans the FIFO queue.  The first order that passes
        *predicate* is removed from the book and returned.

        Returns ``None`` when no matching order exists.
        """
        levels = self._bids if side == Side.BUY else self._asks
        price_list = self._bid_prices if side == Side.BUY else self._ask_prices
        if not price_list:
            return None

        # Iterate in priority order: bids descending, asks ascending
        if side == Side.BUY:
            price_iter = reversed(price_list)
        else:
            price_iter = iter(price_list)

        for price in list(price_iter):
            queue = levels.get(price)
            if queue is None:
                continue
            for order_id in queue:
                order = self._orders.get(order_id)
                if order is not None and predicate(order):
                    queue.remove(order_id)
                    if not queue:
                        levels.pop(price, None)
                        self._remove_price(price_list, price)
                    return self._orders.pop(order_id)
        return None

    def depth_snapshot(self, levels: int = 5) -> dict:
        """Return top *levels* of the bid and ask sides.

        Returns ``{"bids": [(price, total_qty), ...], "asks": [(price, total_qty), ...]}``.
        Bids are sorted descending by price, asks ascending.
        """
        def _aggregate(side_levels: dict[int, deque[str]], prices: list[int], descending: bool) -> list[tuple[int, int]]:
            if descending:
                selected = prices[-levels:] if levels <= len(prices) else prices[:]
                selected = list(reversed(selected))
            else:
                selected = prices[:levels]
            result = []
            for p in selected:
                total = 0
                for oid in side_levels.get(p, []):
                    order = self._orders.get(oid)
                    if order is not None:
                        total += order.remaining
                if total > 0:
                    result.append((p, total))
            return result

        return {
            "bids": _aggregate(self._bids, self._bid_prices, descending=True),
            "asks": _aggregate(self._asks, self._ask_prices, descending=False),
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
        """Return up to *n* worst-priced order IDs owned by *owner_id*.

        Worst = lowest price for bids, highest price for asks.
        """
        levels = self._bids if side == Side.BUY else self._asks
        price_list = self._bid_prices if side == Side.BUY else self._ask_prices
        if not price_list:
            return []

        # Worst prices: ascending for bids (lowest = worst), descending for asks (highest = worst)
        if side == Side.BUY:
            price_iter = iter(price_list)          # ascending → worst first
        else:
            price_iter = reversed(price_list)      # descending → worst first

        result: list[str] = []
        for price in price_iter:
            if len(result) >= n:
                break
            queue = levels.get(price)
            if queue is None:
                continue
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
        """Total quantity of orders at *price* on *side* owned by *owner_id_filter*.

        Used to compute the queue depth of market orders ahead of a strategy
        order at the same price level.

        When *before_seq* is provided, only orders whose ``book_seq`` is
        strictly less than *before_seq* are counted.  This ensures that
        market orders which arrived **after** the strategy order are not
        treated as queue depth ahead of it — matching real FIFO semantics.
        """
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

    @staticmethod
    def _remove_from_queue(queue: deque[str], order_id: str) -> None:
        try:
            queue.remove(order_id)
        except ValueError:
            return

    @staticmethod
    def _remove_price(price_list: list[int], price: int) -> None:
        idx = bisect.bisect_left(price_list, price)
        if idx < len(price_list) and price_list[idx] == price:
            price_list.pop(idx)
