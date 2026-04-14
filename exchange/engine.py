"""Exchange matching engine skeleton with baseline order constraints."""

from __future__ import annotations

from exchange.models import (
    AccountState, ExecutionResult, Fill, MARKET_OWNER_ID,
    Order, OrderStatus, Side, TimeInForce,
)
from exchange.order_book import OrderBook


class ExchangeEngine:
    """Small, test-friendly scaffold for Person C responsibilities."""

    MAKER_FEE_BPS: int = 10     # 10 BPS = 0.1%  (Bitfinex maker fee)
    TAKER_FEE_BPS: int = 20     # 20 BPS = 0.2%  (Bitfinex taker fee)

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

    def apply_market_event(self, event) -> list[Fill]:
        """Apply a Bitfinex L3 MarketEvent to the passive order book.

        Returns any fills that occurred between the incoming market order
        and resting strategy orders.
        """
        return self._get_applicator().apply(event)

    def apply_s3_snapshot(self, snapshot_data: dict) -> None:
        """Seed the book from an S3 snapshot_HHMMSS.log dict."""
        self._get_applicator().apply_s3_snapshot(snapshot_data)

    def _get_applicator(self):
        if not hasattr(self, "_market_event_applicator"):
            from exchange.market_event_applicator import MarketEventApplicator
            self._market_event_applicator = MarketEventApplicator(self)
        return self._market_event_applicator

    def match_incoming_against_strategy(
        self,
        incoming_side: Side,
        price: int,
        quantity: int,
        bitfinex_id: int,
    ) -> tuple[list[Fill], int]:
        """Match incoming market liquidity against resting strategy orders.

        When a new market order arrives from the L3 feed, it may cross
        resting strategy orders.  This method finds and fills those
        crosses in price-time priority (strategy is maker, market is
        taker).

        **Queue position modeling**: before filling a strategy order at a
        given price level, the market-order queue depth ahead of it is
        subtracted from the available incoming quantity.  This prevents
        optimistic fills where in reality market participants would have
        absorbed the aggressor first.

        **Assumption — FIFO within a price level**: the simulation assumes
        that all market orders at the same price level are filled in FIFO
        order before any strategy order.  Real exchanges (including
        Bitfinex) may use different tie-breaking rules within a level.
        This means simulated fill rates at congested levels may differ
        from production.

        Returns ``(fills, remaining_quantity)``.  The caller should place
        ``remaining_quantity`` on the book as a passive market order.
        """
        fills: list[Fill] = []
        remaining = quantity
        opposite_side = Side.BUY if incoming_side == Side.SELL else Side.SELL

        while remaining > 0:
            best = self.book.pop_best_filtered(
                opposite_side,
                lambda o: o.owner_id != MARKET_OWNER_ID,
            )
            if best is None:
                break

            # Check price crossing
            if opposite_side == Side.BUY and best.price < price:
                self.book.add(best)
                break
            if opposite_side == Side.SELL and best.price > price:
                self.book.add(best)
                break

            # Queue position: only market orders that were on the book
            # *before* the strategy order count as ahead in FIFO order.
            market_ahead = self.book.market_depth_ahead(
                opposite_side, best.price, MARKET_OWNER_ID,
                before_seq=best.book_seq,
            )
            effective_remaining = remaining - market_ahead
            if effective_remaining <= 0:
                # Incoming quantity is fully absorbed by market queue ahead
                self.book.add(best)
                break

            qty = min(effective_remaining, best.remaining)
            remaining -= qty
            best.remaining -= qty

            fill = Fill(
                maker_order_id=best.order_id,
                taker_order_id=f"mkt-{bitfinex_id}",
                maker_owner_id=best.owner_id,
                taker_owner_id=MARKET_OWNER_ID,
                maker_side=best.side,
                taker_side=incoming_side,
                price=best.price,
                quantity=qty,
            )
            fills.append(fill)

            if best.remaining > 0:
                best.status = OrderStatus.PARTIALLY_FILLED
                self.book.add(best)
            else:
                best.status = OrderStatus.FILLED

        # Apply accounting for strategy (maker) side only
        for fill in fills:
            maker_state = self.get_account_state(fill.maker_owner_id)
            maker_fee = self._compute_fee(
                fill.price * fill.quantity, self.MAKER_FEE_BPS,
            )
            maker_state.apply_trade(
                side=fill.maker_side,
                price=fill.price,
                quantity=fill.quantity,
                fee=maker_fee,
            )
            self._fee_pool += maker_fee

        return fills, remaining

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
    def _compute_fee(notional: int, fee_bps: int) -> int:
        return (notional * fee_bps) // 10_000 # Fixed-point division to avoid float drift
