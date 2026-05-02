"""
GLFT + Microprice Market Making Strategy
Reference: The micro-price: a high-frequency estimator of future prices (Stoikov, 2018)

Identical to GLFT but replaces the naive mid-price with the order-book microprice
as the reference price for quote computation.

    imbalance   = bid_qty / (bid_qty + ask_qty)
    microprice  = best_ask * imbalance + best_bid * (1 - imbalance)

When bid_qty >> ask_qty → imbalance → 1 → microprice ≈ best_ask (buying pressure)
When ask_qty >> bid_qty → imbalance → 0 → microprice ≈ best_bid (selling pressure)
"""
import math
import uuid
from collections import deque
from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


class GLFTMicropriceStrategy(BaseStrategy):
    """
    GLFT market making model using microprice instead of mid-price.
    All spread and inventory logic is identical to GLFT.
    """

    _VOL_WINDOW = 100
    _MIN_SPREAD = 0.10

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
            raise ValueError(f"risk_aversion must be positive, got {risk_aversion}")
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

    def _time_left(self) -> float:
        if self._session_start_ms is None:
            return 1.0
        elapsed_ms = self.current_local_ts - self._session_start_ms
        remaining = 1.0 - elapsed_ms / self._session_duration_ms
        return max(remaining, 1e-6)

    def _update_volatility(self, ref_price: float, ts_ms: int) -> None:
        self._mid_prices.append(ref_price)
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

        sigma = math.sqrt(weighted_sq_sum) * ref_price
        if math.isfinite(sigma):
            self.sigma = sigma

    def _cancel_active_orders(self) -> None:
        if self.exchange is None:
            return
        for oid in self._active_order_ids:
            self.cancel_order(oid)
        self._active_order_ids.clear()

    @staticmethod
    def _compute_microprice(event: dict) -> float:
        bids = event.get("depth_bids", [])
        asks = event.get("depth_asks", [])

        if bids and asks:
            bid_qty = bids[0][1]
            ask_qty = asks[0][1]
            total = bid_qty + ask_qty
            if total > 0:
                imbalance = bid_qty / total
                return event["best_ask"] * imbalance + event["best_bid"] * (1 - imbalance)

        return (event["best_bid"] + event["best_ask"]) / 2.0

    def calculate_quotes(self, ref_price: float) -> tuple[float | None, float | None]:
        time_left = self._time_left()
        q = self.inventory_position
        Q = self.max_inventory

        if self.sigma == 0.0:
            liquidity_only = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.kappa)
            return ref_price - liquidity_only, ref_price + liquidity_only

        variance_time = self.gamma * (self.sigma ** 2) * time_left
        common_term = (variance_time / 2.0) + (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.kappa)

        inventory_ratio = q / Q
        inventory_adj = inventory_ratio * variance_time

        delta_bid = max(common_term + inventory_adj, self._MIN_SPREAD)
        delta_ask = max(common_term - inventory_adj, self._MIN_SPREAD)

        bid_price = None if q >= Q else ref_price - delta_bid
        ask_price = None if q <= -Q else ref_price + delta_ask

        return bid_price, ask_price

    def on_market_data(self, event: dict) -> None:
        if self._session_start_ms is None:
            self._session_start_ms = event["local_ts_ms"]

        microprice = self._compute_microprice(event)

        if self.sigma != 0.0:
            bid_price, ask_price = self.calculate_quotes(microprice)
        else:
            bid_price = ask_price = None

        self._update_volatility(microprice, event["local_ts_ms"])

        if bid_price is None and ask_price is None:
            return

        soon_to_cancel = tuple(self._active_order_ids)
        self._cancel_active_orders()
        new_ids = []

        if bid_price is not None:
            bid_qty = self.fit_quote("buy", bid_price, self.order_size, exclude_locks_from_orders=soon_to_cancel)
            if bid_qty > 0:
                bid_id = f"glft_mp_bid_{uuid.uuid4().hex[:8]}"
                self.submit_order(bid_price, "buy", bid_qty, order_id=bid_id)
                new_ids.append(bid_id)

        if ask_price is not None:
            ask_qty = self.fit_quote("sell", ask_price, self.order_size, exclude_locks_from_orders=soon_to_cancel)
            if ask_qty > 0:
                ask_id = f"glft_mp_ask_{uuid.uuid4().hex[:8]}"
                self.submit_order(ask_price, "sell", ask_qty, order_id=ask_id)
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
    strategy_id="glft_microprice",
    name="GLFT + Microprice",
    description="Inventory-aware market making using order book imbalance as a price signal via microprice instead of mid-price. (Stoikov, 2018)",
    cls=GLFTMicropriceStrategy,
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
