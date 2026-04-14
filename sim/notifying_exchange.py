from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

from exchange import ExchangeGateway
from exchange.models import AccountState, ExecutionResult, Fill, Order


class FillListener(Protocol):
    def __call__(self, fill: Fill) -> None: ...


@dataclass
class NotifyingExchange:
    """
    Integration adapter that forwards orders to the exchange gateway and
    emits per-fill callbacks back into the strategy layer.
    """

    gateway: ExchangeGateway
    on_fill: Optional[FillListener] = None
    emitted_fills: list[Fill] = field(default_factory=list)

    def submit_order(self, order: Order) -> ExecutionResult:
        result = self.gateway.submit_order(order)
        self._emit_fills(result.fills)
        return result

    def _emit_fills(self, fills: Iterable[Fill]) -> None:
        for fill in fills:
            self.emit_fill(fill)

    def emit_fill(self, fill: Fill) -> None:
        """Record a single fill and notify the strategy."""
        self.emitted_fills.append(fill)
        if self.on_fill is not None:
            self.on_fill(fill)

    def cancel_order(self, order_id: str) -> bool:
        return self.gateway.cancel_order(order_id)
    def get_order(self, order_id: str) -> Optional[Order]:
        return self.gateway.get_order(order_id)
    def get_account_state(self, owner_id: str) -> AccountState:
        return self.gateway.get_account_state(owner_id)
    def get_fee_pool(self) -> int:
        return self.gateway.get_fee_pool()

    