"""
Avellaneda-Stoikov Market Making Strategy
Reference: High-frequency trading in a limit order book (Avellaneda & Stoikov, 2008)
"""
import logging
import math
import uuid
from collections import deque
from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER

logger = logging.getLogger(__name__)


class AvellanedaStoikovStrategy(BaseStrategy):
    """
    Implementation of the Avellaneda-Stoikov market making model.
    Dynamically adjusts bid/ask quotes based on inventory risk and market volatility.

    The model computes quotes in two steps:
      1. Reservation price  r = s - q * gamma * sigma^2 * (T - t)
      2. Optimal half-spread delta = (gamma * sigma^2 * (T - t) / 2) + (1 / gamma) * ln(1 + gamma / k)

    Final quotes:
        bid_price = r - delta
        ask_price = r + delta
    """

    # Volatility estimator: rolling window of mid-price log-returns
    
    _VOL_WINDOW = 100   # number of mid-price samples to keep

    def __init__(
        self,
        risk_aversion: float = 0.001,
        order_size: float = 1.0,
        session_duration: float = 1.0,
        maker_fee_bps: int = 10,
    ):
        super().__init__()

        if risk_aversion <= 0:
            raise ValueError(f"risk_aversion (gamma) must be positive, got {risk_aversion}")
        if session_duration <= 0:
            raise ValueError(f"session_duration must be positive, got {session_duration}")

        # AS model parameters (gamma tuned for dollar-denominated sigma)
        self.gamma = risk_aversion      # Inventory risk aversion (gamma)
        self.k = 1.5                    # Order book liquidity / density parameter (kappa)
        self._maker_fee_rate = maker_fee_bps / 10_000  # 10 BPS = 0.001

        # Volatility state
        self.sigma = 0.0                # Current volatility estimate (updated on each tick)
        self._mid_prices: deque = deque(maxlen=self._VOL_WINDOW)
        self._mid_timestamps: deque = deque(maxlen=self._VOL_WINDOW)

        # Inventory
        self.inventory_position = 0.0
        self.order_size = order_size

        # Time tracking — used to compute (T - t) ∈ [0, 1]
        # Initialised on the first market-data tick (simulation clock, ms)
        self._session_start_ms: float | None = None
        self._session_duration_ms: float = session_duration * 1000.0  # convert s → ms

        # Active order tracking for cancel-replace cycle
        self._active_order_ids: list[str] = []

        # One-shot config audit: runs on the first tick where mid_price is
        # known and the account is enforced.  Surfaces likely misconfiguration
        # (e.g., clip too large for balance) early instead of letting the
        # strategy quietly under-quote for the whole run.
        self._config_validated: bool = False

    # Internal helpers
    
    def _time_left(self) -> float:
        """
        Returns T - t normalised to [0, 1] using the *simulation* clock.
        Clamps to a small positive floor so formulas stay well-defined near T.
        """
        if self._session_start_ms is None:
            return 1.0  # before first tick
        elapsed_ms = self.current_local_ts - self._session_start_ms
        remaining = 1.0 - elapsed_ms / self._session_duration_ms
        return max(remaining, 1e-6)

    def _update_volatility(self, mid_price: float, ts_ms: int) -> None:
        """
        Time-weighted realized volatility estimator in dollar terms.

        The AS model requires sigma in the same units as the price process
        (dollars), not dimensionless log-returns.  We compute the log-return
        volatility and scale by mid_price: sigma_$ = sigma_logret * S.

        This correctly handles irregular tick spacing — a burst of updates
        within one millisecond contributes less than the same number spread
        over a second.

        Requires at least 2 samples; sigma stays 0.0 until then.
        """
        self._mid_prices.append(mid_price)
        self._mid_timestamps.append(ts_ms)
        n = len(self._mid_prices)
        if n < 2:
            return

        prices = list(self._mid_prices)
        timestamps = list(self._mid_timestamps)

        total_dt_ms = timestamps[-1] - timestamps[0]
        if total_dt_ms <= 0:
            return  # all samples at the same timestamp

        weighted_sq_sum = 0.0
        for i in range(1, n):
            dt_ms = timestamps[i] - timestamps[i - 1]
            if dt_ms <= 0:
                continue
            log_ret = math.log(prices[i] / prices[i - 1])
            # Weight = fraction of total window this interval occupies
            weight = dt_ms / total_dt_ms
            weighted_sq_sum += weight * (log_ret ** 2)

        # Scale by mid_price to convert log-return vol → dollar vol
        sigma = math.sqrt(weighted_sq_sum) * mid_price
        if math.isfinite(sigma):
            self.sigma = sigma

    def _audit_config(self, mid_price: float) -> None:
        """One-shot sanity check after the first market tick.

        Only meaningful when balance enforcement is on; otherwise the strategy
        runs unconstrained and clip size is whatever the user set.
        """
        if not self.is_balance_enforced():
            return

        cash_usd = self.available_quote_usd()
        base_btc = self.available_base_btc()
        clip_notional = self.order_size * mid_price

        # Hard error: cannot quote either side at the minimum lot.
        min_buy_notional = self.MIN_QUANTITY_BTC * mid_price * (
            1.0 + self.TAKER_FEE_BPS_ESTIMATE / 10_000.0
        )
        if cash_usd < min_buy_notional and base_btc < self.MIN_QUANTITY_BTC:
            logger.error(
                "Account too small to quote either side at mid=$%.2f. "
                "Need at least $%.2f cash or %.6f BTC; have $%.2f / %.6f BTC.",
                mid_price, min_buy_notional, self.MIN_QUANTITY_BTC,
                cash_usd, base_btc,
            )
            return

        # Warning: clip oversized relative to available capacity. fit_quote
        # will silently auto-shrink, but the user may want to know.
        if cash_usd > 0 and clip_notional > 0.5 * cash_usd:
            logger.warning(
                "Bid clip ($%.2f notional) exceeds 50%% of available cash ($%.2f). "
                "Bids will auto-shrink to fit; consider lowering --order-size.",
                clip_notional, cash_usd,
            )
        if base_btc > 0 and self.order_size > 0.5 * base_btc:
            logger.warning(
                "Ask clip (%.6f BTC) exceeds 50%% of available base (%.6f BTC). "
                "Asks will auto-shrink to fit.",
                self.order_size, base_btc,
            )

    def _cancel_active_orders(self) -> None:
        """Cancel all outstanding strategy quotes on the exchange."""
        if self.exchange is None:
            return
        for oid in self._active_order_ids:
            self.cancel_order(oid)
        self._active_order_ids.clear()

    # Core AS formulas
    
    def calculate_reservation_price(self, mid_price: float, time_left: float) -> float:
        """
        Inventory-adjusted theoretical mid-price.
        r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
        """
        risk_adjustment = self.inventory_position * self.gamma * (self.sigma ** 2) * time_left
        return mid_price - risk_adjustment

    def calculate_optimal_half_spread(self, time_left: float) -> float:
        """
        Calculates the optimal half-spread to be applied symmetrically around the reservation price.
        delta = (gamma * sigma^2 * (T - t) / 2) + (1 / gamma) * ln(1 + gamma / k)
        """
        risk_term = (self.gamma * (self.sigma ** 2) * time_left) / 2.0
        liquidity_term = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.k)
        return risk_term + liquidity_term

    
    def calculate_quotes(self, mid_price: float) -> tuple[float, float]:
        """
        Returns (bid_price, ask_price) using the full AS model.
        """
        if self.sigma == 0.0:
            # Volatility not yet estimated — fall back to symmetric spread
            half_spread = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.k)
            return mid_price - half_spread, mid_price + half_spread

        time_left = self._time_left()
        reservation = self.calculate_reservation_price(mid_price, time_left)
        half_spread = self.calculate_optimal_half_spread(time_left)

        bid_price = reservation - half_spread
        ask_price = reservation + half_spread

        return bid_price, ask_price

    # Event handlers
    
    def on_market_data(self, event: dict) -> None:
        """
        Triggered when new market data arrives.
        Updates volatility estimate and recomputes/submits quotes.
        """
        # Seed session start on first tick (simulation clock)
        if self._session_start_ms is None:
            self._session_start_ms = event["local_ts_ms"]

        # Keep the strategy's inventory_position in sync with the engine's
        # authoritative base_position.  Without this, a seeded initial base
        # is invisible to the reservation-price math (q stays 0 even though
        # the account is long), and any drift from on_order_fill rounding
        # would compound over time.  Tolerant of test doubles that omit
        # get_account_state.
        get_state = getattr(self.exchange, "get_account_state", None) if self.exchange else None
        if get_state is not None:
            self.inventory_position = get_state(self.owner_id).base_position / QUANTITY_MULTIPLIER

        mid_price = (event["best_bid"] + event["best_ask"]) / 2.0

        if not self._config_validated:
            self._audit_config(mid_price)
            self._config_validated = True

        # Quote using volatility from *prior* ticks only (no look-ahead bias),
        # then update volatility with the current tick afterwards.
        if self.sigma != 0.0:
            bid_price, ask_price = self.calculate_quotes(mid_price)
        else:
            bid_price = ask_price = None

        self._update_volatility(mid_price, event["local_ts_ms"])

        if bid_price is None:
            return

        # Capture the orders we're about to cancel so fit_quote can treat
        # their still-held locks as already released (cancels travel through
        # the latency queue and may not have drained yet at this instant).
        soon_to_cancel = tuple(self._active_order_ids)
        self._cancel_active_orders()

        bid_qty = self.fit_quote(
            "buy", bid_price, self.order_size,
            exclude_locks_from_orders=soon_to_cancel,
        )
        ask_qty = self.fit_quote(
            "sell", ask_price, self.order_size,
            exclude_locks_from_orders=soon_to_cancel,
        )

        new_active: list[str] = []
        if bid_qty > 0:
            bid_id = f"as_bid_{uuid.uuid4().hex[:8]}"
            self.submit_order(bid_price, "buy", bid_qty, order_id=bid_id)
            new_active.append(bid_id)
        if ask_qty > 0:
            ask_id = f"as_ask_{uuid.uuid4().hex[:8]}"
            self.submit_order(ask_price, "sell", ask_qty, order_id=ask_id)
            new_active.append(ask_id)

        self._active_order_ids = new_active

    def on_order_fill(self, fill_event) -> None:
        """
        Triggered when an order is executed.
        Updates inventory based on which side was filled.
        """
        filled_amount = fill_event.quantity / QUANTITY_MULTIPLIER

        if fill_event.maker_owner_id == "default":
            side = fill_event.maker_side
        elif fill_event.taker_owner_id == "default":
            side = fill_event.taker_side
        else:
            return  # not our order

        if side == Side.BUY:
            self.inventory_position += filled_amount
        else:
            self.inventory_position -= filled_amount


# ── Registry registration ────────────────────────────────────────────────
from strategy.registry import register

register(
    strategy_id="avellaneda_stoikov",
    name="Avellaneda-Stoikov",
    description="Inventory-aware market making that skews bid/ask quotes symmetrically based on volatility and risk aversion. (Avellaneda & Stoikov, 2008)",
    cls=AvellanedaStoikovStrategy,
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
    ],
    liquidity_attr="k",
    category="Stochastic Control",
    capital_check={"mode": "clip", "multiplier": 5},  # inventory accumulates without a hard limit — 5× clip provides buffer for build-up
)