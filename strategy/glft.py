"""
Guéant-Lehalle-Fernandez-Tapia (GLFT) Market Making Strategy
Reference: Dealing with the Inventory Risk (Guéant, Lehalle, Fernandez-Tapia, 2012)

Key differences from Avellaneda-Stoikov:
  1. Asymmetric spreads: δ_bid ≠ δ_ask, each adjusted directly by inventory ratio q/Q
  2. Configurable kappa (κ): A-S hardcodes it at 1.5; GLFT exposes it as a parameter
  3. Inventory limits (Q_max): strategy stops quoting one side when limit is reached
"""
import math
import uuid
from collections import deque
from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


class GLFTStrategy(BaseStrategy):
    """
    GLFT market making model with asymmetric inventory-aware spreads.

    Quotes are computed directly from mid-price s using asymmetric half-spreads:

        inventory_adj = (q / Q) * gamma * sigma^2 * (T - t)
        common_term   = (gamma * sigma^2 * (T - t) / 2) + (1 / gamma) * ln(1 + gamma / kappa)

        delta_bid = common_term + inventory_adj   (wider when long → discourages buying more)
        delta_ask = common_term - inventory_adj   (narrower when long → encourages selling)

        bid = s - delta_bid
        ask = s + delta_ask

    Inventory limits:
        q >=  Q  → no bid posted (already at long limit)
        q <= -Q  → no ask posted (already at short limit)
    """

    _VOL_WINDOW = 100
    _MIN_SPREAD = 0.10  # minimum half-spread in USD to avoid negative ask spreads

    def __init__(
        self,
        risk_aversion: float = 0.001,
        order_size: float = 0.01,
        session_duration: float = 86400.0,
        max_inventory: float = 0.5,
        kappa: float = 1.5,
        maker_fee_bps: int = 10,
    ):
        super().__init__()

        if risk_aversion <= 0:
            raise ValueError(f"risk_aversion (gamma) must be positive, got {risk_aversion}")
        if session_duration <= 0:
            raise ValueError(f"session_duration must be positive, got {session_duration}")
        if max_inventory <= 0:
            raise ValueError(f"max_inventory must be positive, got {max_inventory}")
        if kappa <= 0:
            raise ValueError(f"kappa must be positive, got {kappa}")

        self.gamma = risk_aversion
        self.kappa = kappa
        self.max_inventory = max_inventory
        self.order_size = order_size
        self._maker_fee_rate = maker_fee_bps / 10_000

        self.sigma = 0.0
        self._mid_prices: deque = deque(maxlen=self._VOL_WINDOW)
        self._mid_timestamps: deque = deque(maxlen=self._VOL_WINDOW)

        self.inventory_position = 0.0

        self._session_start_ms: float | None = None
        self._session_duration_ms: float = session_duration * 1000.0

        self._active_order_ids: list[str] = []

    # ── Internal helpers ──────────────────────────────────────────────────

    def _time_left(self) -> float:
        if self._session_start_ms is None:
            return 1.0
        elapsed_ms = self.current_local_ts - self._session_start_ms
        remaining = 1.0 - elapsed_ms / self._session_duration_ms
        return max(remaining, 1e-6)

    def _update_volatility(self, mid_price: float, ts_ms: int) -> None:
        """Time-weighted realized volatility in dollar terms (same method as A-S)."""
        self._mid_prices.append(mid_price)
        self._mid_timestamps.append(ts_ms)
        n = len(self._mid_prices)
        if n < 2:
            return

        prices = list(self._mid_prices)
        timestamps = list(self._mid_timestamps)

        total_dt_ms = timestamps[-1] - timestamps[0]
        if total_dt_ms <= 0:
            return

        weighted_sq_sum = 0.0
        for i in range(1, n):
            dt_ms = timestamps[i] - timestamps[i - 1]
            if dt_ms <= 0:
                continue
            log_ret = math.log(prices[i] / prices[i - 1])
            weight = dt_ms / total_dt_ms
            weighted_sq_sum += weight * (log_ret ** 2)

        sigma = math.sqrt(weighted_sq_sum) * mid_price
        if math.isfinite(sigma):
            self.sigma = sigma

    def _cancel_active_orders(self) -> None:
        if self.exchange is None:
            return
        for oid in self._active_order_ids:
            self.cancel_order(oid)
        self._active_order_ids.clear()

    # ── Core GLFT formulas ────────────────────────────────────────────────

    def calculate_quotes(self, mid_price: float) -> tuple[float | None, float | None]:
        """
        Returns (bid_price, ask_price). Either may be None if inventory limit is reached.

        GLFT asymmetric spread:
            inventory_adj = (q / Q) * gamma * sigma^2 * (T-t)
            common_term   = (gamma * sigma^2 * (T-t) / 2) + (1/gamma) * ln(1 + gamma/kappa)
            delta_bid     = common_term + inventory_adj
            delta_ask     = common_term - inventory_adj
        """
        time_left = self._time_left()
        q = self.inventory_position
        Q = self.max_inventory

        if self.sigma == 0.0:
            # No volatility estimate yet — use symmetric liquidity term only
            liquidity_only = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.kappa)
            bid = mid_price - liquidity_only
            ask = mid_price + liquidity_only
            return bid, ask

        variance_time = self.gamma * (self.sigma ** 2) * time_left
        common_term = (variance_time / 2.0) + (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.kappa)

        # Inventory adjustment: normalized to max_inventory so the effect is bounded
        inventory_ratio = q / Q
        inventory_adj = inventory_ratio * variance_time

        delta_bid = common_term + inventory_adj
        delta_ask = common_term - inventory_adj

        # Floor at minimum spread to prevent negative or zero spreads
        delta_bid = max(delta_bid, self._MIN_SPREAD)
        delta_ask = max(delta_ask, self._MIN_SPREAD)

        # Hard inventory limits: stop quoting the side that would worsen position
        bid_price = None if q >= Q else mid_price - delta_bid
        ask_price = None if q <= -Q else mid_price + delta_ask

        return bid_price, ask_price

    # ── Event handlers ────────────────────────────────────────────────────

    def on_market_data(self, event: dict) -> None:
        if self._session_start_ms is None:
            self._session_start_ms = event["local_ts_ms"]

        mid_price = (event["best_bid"] + event["best_ask"]) / 2.0

        if self.sigma != 0.0:
            bid_price, ask_price = self.calculate_quotes(mid_price)
        else:
            bid_price = ask_price = None

        self._update_volatility(mid_price, event["local_ts_ms"])

        if bid_price is None and ask_price is None:
            return

        self._cancel_active_orders()
        new_ids = []

        if bid_price is not None:
            bid_id = f"glft_bid_{uuid.uuid4().hex[:8]}"
            self.submit_order(bid_price, "buy", self.order_size, order_id=bid_id)
            new_ids.append(bid_id)

        if ask_price is not None:
            ask_id = f"glft_ask_{uuid.uuid4().hex[:8]}"
            self.submit_order(ask_price, "sell", self.order_size, order_id=ask_id)
            new_ids.append(ask_id)

        self._active_order_ids = new_ids

    def on_order_fill(self, fill_event) -> None:
        filled_amount = fill_event.quantity / QUANTITY_MULTIPLIER

        if fill_event.maker_owner_id == "default":
            side = fill_event.maker_side
        elif fill_event.taker_owner_id == "default":
            side = fill_event.taker_side
        else:
            return

        if side == Side.BUY:
            self.inventory_position += filled_amount
        else:
            self.inventory_position -= filled_amount


# ── Registry registration ────────────────────────────────────────────────
from strategy.registry import register

register(
    strategy_id="glft",
    name="GLFT",
    description="Inventory-aware market making with asymmetric per-side quote skew and hard position limits. (Guéant, Lehalle & Fernandez-Tapia, 2012)",
    cls=GLFTStrategy,
    parameters=[
        {"name": "risk_aversion", "label": "Risk Aversion (gamma)", "type": "float",
         "default": 0.001, "min": 0.0001, "max": 1.0, "step": 0.0001,
         "description": "Controls how aggressively the strategy skews quotes to reduce inventory."},
        {"name": "order_size", "label": "Order Size (BTC)", "type": "float",
         "default": 0.01, "min": 0.001, "max": 1.0, "step": 0.001,
         "description": "Size of each bid/ask quote in BTC."},
        {"name": "session_duration", "label": "Session Duration (s)", "type": "float",
         "default": 86400, "min": 3600, "max": 86400, "step": 3600,
         "description": "Trading session length in seconds."},
        {"name": "max_inventory", "label": "Max Inventory (BTC)", "type": "float",
         "default": 0.5, "min": 0.01, "max": 10.0, "step": 0.01,
         "description": "Hard inventory limit. Strategy stops quoting one side when this is reached."},
        {"name": "kappa", "label": "Kappa", "type": "float",
         "default": 1.5, "min": 0.1, "max": 10.0, "step": 0.1,
         "description": "Order book liquidity/density parameter."},
    ],
    liquidity_attr="kappa",
    category="Stochastic Control",
)
