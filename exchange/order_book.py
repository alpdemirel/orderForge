"""Deterministic in-memory order book skeleton."""

from __future__ import annotations

from collections import defaultdict, deque

from exchange.models import Order, Side


class OrderBook:
    """Simple price-time priority scaffold for resting orders."""

    def __init__(self) -> None:
        self._bids: dict[int, deque[str]] = defaultdict(deque)
        self._asks: dict[int, deque[str]] = defaultdict(deque)
        self._orders: dict[str, Order] = {}

    @property
    def orders(self) -> dict[str, Order]:
        return self._orders

    def add(self, order: Order) -> None:
        if order.order_id in self._orders:
            raise ValueError(f"Order already exists: {order.order_id}")

        self._orders[order.order_id] = order
        levels = self._bids if order.side == Side.BUY else self._asks
        levels[order.price].append(order.order_id)

    def cancel(self, order_id: str) -> Order | None:
        order = self._orders.pop(order_id, None)
        if order is None:
            return None

        levels = self._bids if order.side == Side.BUY else self._asks
        queue = levels.get(order.price)
        if queue is not None:
            self._remove_from_queue(queue, order_id)
            if not queue:
                levels.pop(order.price, None)

        return order

    def best_bid(self) -> int | None:
        return max(self._bids) if self._bids else None

    def best_ask(self) -> int | None:
        return min(self._asks) if self._asks else None

    def pop_best(self, side: Side) -> Order | None:
        price = self.best_bid() if side == Side.BUY else self.best_ask()
        if price is None:
            return None

        levels = self._bids if side == Side.BUY else self._asks
        queue = levels[price]
        order_id = queue.popleft()
        if not queue:
            levels.pop(price, None)
        return self._orders.pop(order_id, None)

    @staticmethod
    def _remove_from_queue(queue: deque[str], order_id: str) -> None:
        try:
            queue.remove(order_id)
        except ValueError:
            return

