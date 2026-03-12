"""Exchange matching engine skeleton with baseline order constraints."""

from __future__ import annotations

from exchange.models import AccountState, ExecutionResult, Fill, Order, OrderStatus, Side, TimeInForce
from exchange.order_book import OrderBook


class ExchangeEngine:
    """Small, test-friendly scaffold for Person C responsibilities."""

    MAKER_FEE_BPS = 0.0
    TAKER_FEE_BPS = 5.0

    def __init__(self) -> None:
        self.book = OrderBook()
        self._accounts: dict[str, AccountState] = {}
        self._fee_pool: int = 0

    def submit_order(self, order: Order) -> ExecutionResult:
        if order.quantity <= 0:
            order.status = OrderStatus.REJECTED
            return ExecutionResult(False, reason="quantity_must_be_positive")

        # Post-only should never execute immediately in this baseline scaffold.
        if order.tif == TimeInForce.POST_ONLY and self._would_cross(order):
            order.status = OrderStatus.REJECTED
            return ExecutionResult(False, reason="post_only_would_cross")

        fills = self._match(order)
        taker_fee, maker_fees = self._apply_fill_accounting(order, fills)
        if order.remaining <= 0:
            order.status = OrderStatus.FILLED
            return ExecutionResult(True, fills=fills, taker_fee=taker_fee, maker_fees=maker_fees)

        if order.tif == TimeInForce.IOC:
            order.status = OrderStatus.CANCELED
            return ExecutionResult(
                True,
                reason="ioc_unfilled_canceled",
                fills=fills,
                taker_fee=taker_fee,
                maker_fees=maker_fees,
            )

        if fills:
            order.status = OrderStatus.PARTIALLY_FILLED
        self.book.add(order)
        return ExecutionResult(True, fills=fills, taker_fee=taker_fee, maker_fees=maker_fees)

    def cancel_order(self, order_id: str) -> bool:
        order = self.book.cancel(order_id)
        if order is None:
            return False
        order.status = OrderStatus.CANCELED
        return True

    def get_order(self, order_id: str) -> Order | None:
        return self.book.orders.get(order_id)

    def get_account_state(self, owner_id: str) -> AccountState:
        return self._accounts.setdefault(owner_id, AccountState(owner_id=owner_id))

    def get_fee_pool(self) -> int:
        return self._fee_pool

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
                    maker_owner_id=maker.owner_id,
                    taker_owner_id=taker.owner_id,
                    maker_side=maker.side,
                    taker_side=taker.side,
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

    def _apply_fill_accounting(self, taker: Order, fills: list[Fill]) -> tuple[int, dict[str, int]]:
        taker_state = self.get_account_state(taker.owner_id)
        taker_fee_total = 0
        maker_fees: dict[str, int] = {}

        for fill in fills:
            notional = fill.price * fill.quantity
            maker_fee = self._compute_fee(notional, self.MAKER_FEE_BPS)
            taker_fee = self._compute_fee(notional, self.TAKER_FEE_BPS)

            maker_state = self.get_account_state(fill.maker_owner_id)
            maker_state.apply_trade(
                side=fill.maker_side,
                price=fill.price,
                quantity=fill.quantity,
                fee=maker_fee,
            )
            taker_state.apply_trade(
                side=fill.taker_side,
                price=fill.price,
                quantity=fill.quantity,
                fee=taker_fee,
            )

            self._fee_pool += maker_fee + taker_fee
            taker_fee_total += taker_fee
            maker_fees[fill.maker_owner_id] = maker_fees.get(fill.maker_owner_id, 0) + maker_fee

        return taker_fee_total, maker_fees

    @staticmethod
    def _compute_fee(notional: int, fee_bps: float) -> int:
        return round(notional * (fee_bps / 10_000.0))
