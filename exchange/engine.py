"""Exchange matching engine skeleton with baseline order constraints."""

from __future__ import annotations

from exchange.models import ExecutionResult, Fill, Order, OrderStatus, Side, TimeInForce
from exchange.order_book import OrderBook


class ExchangeEngine:
    """Small, test-friendly scaffold for Person C responsibilities."""

    def __init__(self) -> None:
        self.book = OrderBook()

    def submit_order(self, order: Order) -> ExecutionResult:
        if order.quantity <= 0:
            order.status = OrderStatus.REJECTED
            return ExecutionResult(False, reason="quantity_must_be_positive")

        # Post-only should never execute immediately in this baseline scaffold.
        if order.tif == TimeInForce.POST_ONLY and self._would_cross(order):
            order.status = OrderStatus.REJECTED
            return ExecutionResult(False, reason="post_only_would_cross")

        fills = self._match(order)
        if order.remaining <= 0:
            order.status = OrderStatus.FILLED
            return ExecutionResult(True, fills=fills)

        if order.tif == TimeInForce.IOC:
            order.status = OrderStatus.CANCELED
            return ExecutionResult(True, reason="ioc_unfilled_canceled", fills=fills)

        if fills:
            order.status = OrderStatus.PARTIALLY_FILLED
        self.book.add(order)
        return ExecutionResult(True, fills=fills)

    def cancel_order(self, order_id: str) -> bool:
        order = self.book.cancel(order_id)
        if order is None:
            return False
        order.status = OrderStatus.CANCELED
        return True

    def get_order(self, order_id: str) -> Order | None:
        return self.book.orders.get(order_id)

    def _would_cross(self, order: Order) -> bool:
        best_ask = self.book.best_ask()
        best_bid = self.book.best_bid()
        if order.side == Side.BUY:
            return best_ask is not None and order.price >= best_ask
        return best_bid is not None and order.price <= best_bid

    def _match(self, taker: Order) -> list[Fill]:
        fills: list[Fill] = []

        while taker.remaining > 0:
            maker_side = Side.SELL if taker.side == Side.BUY else Side.BUY
            maker = self.book.pop_best(maker_side)
            if maker is None:
                break

            if taker.side == Side.BUY and taker.price < maker.price:
                self.book.add(maker)
                break
            if taker.side == Side.SELL and taker.price > maker.price:
                self.book.add(maker)
                break

            quantity = min(taker.remaining, maker.remaining)
            taker.remaining -= quantity
            maker.remaining -= quantity
            fills.append(
                Fill(
                    maker_order_id=maker.order_id,
                    taker_order_id=taker.order_id,
                    price=maker.price,
                    quantity=quantity,
                )
            )

            if maker.remaining > 0:
                maker.status = OrderStatus.PARTIALLY_FILLED
                self.book.add(maker)
            else:
                maker.status = OrderStatus.FILLED

        return fills

