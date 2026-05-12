"""
Fixed Spread Market Making Strategy (Baseline)

Places symmetric bid/ask limit orders at a fixed distance from mid-price.
No inventory adjustment, no volatility estimation — pure constant spread.

    bid = mid - half_spread
    ask = mid + half_spread
"""
import uuid
from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


class FixedSpreadStrategy(BaseStrategy):
    """
    Baseline market making strategy with a constant symmetric spread.
    Used as a performance reference for more sophisticated strategies.
    """

    def __init__(
        self,
        half_spread: float = 1.0,
        order_size: float = 0.01,
    ):
        super().__init__()

        if half_spread <= 0:
            raise ValueError(f"half_spread must be positive, got {half_spread}")
        if order_size <= 0:
            raise ValueError(f"order_size must be positive, got {order_size}")

        self.half_spread = half_spread
        self.order_size = order_size
        self._active_order_ids: list[str] = []

    def _cancel_active_orders(self) -> None:
        if self.exchange is None:
            return
        for oid in self._active_order_ids:
            self.cancel_order(oid)
        self._active_order_ids.clear()

    def on_market_data(self, event: dict) -> None:
        mid = (event["best_bid"] + event["best_ask"]) / 2.0

        bid_price = mid - self.half_spread
        ask_price = mid + self.half_spread

        if bid_price <= 0:
            return

        soon_to_cancel = tuple(self._active_order_ids)
        self._cancel_active_orders()
        new_ids = []

        bid_qty = self.fit_quote("buy", bid_price, self.order_size, exclude_locks_from_orders=soon_to_cancel)
        if bid_qty > 0:
            bid_id = f"fs_bid_{uuid.uuid4().hex[:8]}"
            self.submit_order(bid_price, "buy", bid_qty, order_id=bid_id)
            new_ids.append(bid_id)

        ask_qty = self.fit_quote("sell", ask_price, self.order_size, exclude_locks_from_orders=soon_to_cancel)
        if ask_qty > 0:
            ask_id = f"fs_ask_{uuid.uuid4().hex[:8]}"
            self.submit_order(ask_price, "sell", ask_qty, order_id=ask_id)
            new_ids.append(ask_id)

        self._active_order_ids = new_ids

    def on_order_fill(self, fill_event) -> None:
        pass  # no inventory tracking needed for baseline


# ── Registry registration ────────────────────────────────────────────────
from strategy.registry import register

register(
    strategy_id="fixed_spread",
    name="Fixed Spread",
    description="Baseline market making with a constant symmetric spread around mid-price. No inventory or volatility adjustment.",
    cls=FixedSpreadStrategy,
    parameters=[
        {"name": "half_spread", "label": "Half Spread (USD)", "type": "float",
         "default": 1.0, "min": 0.1, "max": 100.0, "step": 0.1,
         "description": "Fixed distance from mid-price to each quote in USD."},
        {"name": "order_size", "label": "Order Size (BTC)", "type": "float",
         "default": 0.01, "min": 0.001, "max": 1.0, "step": 0.001,
         "description": "Size of each bid/ask quote in BTC."},
    ],
    liquidity_attr="half_spread",
    category="Baseline",
    capital_check={"mode": "clip", "multiplier": 2},  # symmetric quoting, no inventory accumulation — 2× clip is sufficient
)
