"""Strategy-facing exchange API skeleton."""

from __future__ import annotations

from exchange.engine import ExchangeEngine
from exchange.models import AccountState, ExecutionResult, Fill, Order


class ExchangeGateway:
    """Thin wrapper so strategy code avoids touching engine internals."""

    def __init__(self, engine: ExchangeEngine | None = None) -> None:
        self._engine = engine or ExchangeEngine()

    def submit_order(self, order: Order) -> ExecutionResult:
        return self._engine.submit_order(order)

    def cancel_order(self, order_id: str) -> bool:
        return self._engine.cancel_order(order_id)

    def get_order(self, order_id: str) -> Order | None:
        return self._engine.get_order(order_id)

    def get_account_state(self, owner_id: str) -> AccountState:
        return self._engine.get_account_state(owner_id)

    def get_fee_pool(self) -> int:
        return self._engine.get_fee_pool()

    def apply_market_event(self, event) -> list[Fill]:
        """Feed a Bitfinex L3 MarketEvent into the book via the engine."""
        return self._engine.apply_market_event(event)

    def apply_s3_snapshot(self, snapshot_data: dict) -> None:
        """Seed the book from an S3 snapshot_HHMMSS.log dict."""
        self._engine.apply_s3_snapshot(snapshot_data)

