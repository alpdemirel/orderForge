"""
Avellaneda-Stoikov Market Making Strategy
Reference: High-frequency trading in a limit order book (Avellaneda & Stoikov, 2008)

Improvements over the baseline:
  1. Requote threshold: only cancel-replace when mid moves >= requote_threshold USD,
     reducing quote churn, improving quote survival rate, and lowering staleness.
  2. Momentum EMA: two exponential moving averages (fast/slow) detect short-term trends.
     When the market is trending up, the reservation price is shifted higher so the
     strategy avoids accumulating inventory against the trend, reducing adverse selection.
  3. Force-requote after fills: inventory changes always trigger an immediate requote
     so the strategy adapts its skew without waiting for the price to move.
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

    Additional:
      - Reservation price is adjusted by a momentum EMA signal to follow short-term trends.
      - Requote threshold prevents unnecessary cancel-replace churn on quiet ticks.
    """

    # Volatility estimator: rolling window of mid-price log-returns
    _VOL_WINDOW = 100   # number of mid-price samples to keep

    # Momentum EMA alphas — fast responds to ~10 ticks, slow to ~100 ticks
    _FAST_ALPHA = 0.10
    _SLOW_ALPHA = 0.01
    # When |momentum| exceeds this threshold (as fraction of slow_ema),
    # halt the adverse side entirely instead of just skewing.
    _HALT_THRESHOLD = 0.0003

    def __init__(
        self,
        risk_aversion: float = 0.001,
        order_size: float = 1.0,
        session_duration: float = 1.0,
        maker_fee_bps: int = 10,
        requote_threshold: float = 1.0,
        trend_skew: float = 0.5,
    ):
        super().__init__()

        if risk_aversion <= 0:
            raise ValueError(f"risk_aversion (gamma) must be positive, got {risk_aversion}")
        if session_duration <= 0:
            raise ValueError(f"session_duration must be positive, got {session_duration}")
        if requote_threshold < 0:
            raise ValueError(f"requote_threshold must be >= 0, got {requote_threshold}")

        # AS model parameters (gamma tuned for dollar-denominated sigma)
        self.gamma = risk_aversion      # Inventory risk aversion (gamma)
        self.k = 1.5                    # Order book liquidity / density parameter (kappa)
        self._maker_fee_rate = maker_fee_bps / 10_000  # 10 BPS = 0.001

        # Requote threshold: only cancel-replace when mid moves by this much (USD)
        self.requote_threshold = requote_threshold
        self._last_quoted_mid: float | None = None
        self._force_requote: bool = False  # set True after a fill

        # Trend skew: fraction of half-spread to shift the reservation price per unit
        # of normalised EMA momentum.  0 = off, 1 = very aggressive.
        self.trend_skew = trend_skew
        self._fast_ema: float | None = None
        self._slow_ema: float | None = None

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

    # ── Internal helpers ──────────────────────────────────────────────────

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

    def _update_momentum(self, mid_price: float) -> None:
        """Update fast and slow EMAs for momentum detection."""
        if self._fast_ema is None:
            self._fast_ema = mid_price
            self._slow_ema = mid_price
            return
        self._fast_ema = self._FAST_ALPHA * mid_price + (1 - self._FAST_ALPHA) * self._fast_ema
        self._slow_ema = self._SLOW_ALPHA * mid_price + (1 - self._SLOW_ALPHA) * self._slow_ema

    def _momentum_adjustment(self, half_spread: float) -> float:
        """
        Returns a dollar price adjustment to add to the reservation price.

        Positive = uptrend → shift quotes higher (widen bid, let price come to ask).
        Negative = downtrend → shift quotes lower (widen ask, let price come to bid).

        The adjustment is capped at ±3× half_spread for strong trend-following.
        """
        if self._fast_ema is None or self._slow_ema is None or self._slow_ema == 0:
            return 0.0
        if self.trend_skew <= 0:
            return 0.0
        # Normalised momentum: (fast - slow) / slow, then scale
        momentum = (self._fast_ema - self._slow_ema) / self._slow_ema
        # Scale to be meaningful relative to price level
        adj = momentum * self.trend_skew * self._slow_ema
        # Allow up to 3× half_spread of skew for aggressive trend following
        cap = half_spread * 3.0
        return max(-cap, min(cap, adj))

    def _should_halt_side(self) -> str | None:
        """
        If the trend is strong enough, return 'bid' or 'ask' to halt that side.
        Returns None if both sides should be quoted.
        """
        if self._fast_ema is None or self._slow_ema is None or self._slow_ema == 0:
            return None
        if self.trend_skew <= 0:
            return None
        momentum = (self._fast_ema - self._slow_ema) / self._slow_ema
        if momentum > self._HALT_THRESHOLD:
            return 'bid'   # uptrend → halt bids to avoid buying into rising prices
        if momentum < -self._HALT_THRESHOLD:
            return 'ask'   # downtrend → halt asks to avoid selling into falling prices
        return None

    def _needs_requote(self, mid_price: float) -> bool:
        """Return True if we should cancel and replace current quotes."""
        if self._force_requote:
            return True
        if self._last_quoted_mid is None:
            return True
        return abs(mid_price - self._last_quoted_mid) >= self.requote_threshold

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

    # ── Core AS formulas ──────────────────────────────────────────────────

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

    def calculate_quotes(self, mid_price: float) -> tuple[float | None, float | None]:
        """
        Returns (bid_price, ask_price) using the full AS model with momentum adjustment.
        Either may be None if the momentum filter halts one side.
        """
        if self.sigma == 0.0:
            # Volatility not yet estimated — fall back to symmetric spread
            half_spread = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.k)
            return mid_price - half_spread, mid_price + half_spread

        time_left = self._time_left()
        reservation = self.calculate_reservation_price(mid_price, time_left)
        half_spread = self.calculate_optimal_half_spread(time_left)

        # Shift reservation price to follow momentum — reduces adverse selection
        # on trending days by quoting in the direction of the trend.
        trend_adj = self._momentum_adjustment(half_spread)
        reservation += trend_adj

        bid_price = reservation - half_spread
        ask_price = reservation + half_spread

        # In strong trends, halt the adverse side entirely
        halted = self._should_halt_side()
        if halted == 'bid':
            bid_price = None
        elif halted == 'ask':
            ask_price = None

        return bid_price, ask_price

    # ── Event handlers ────────────────────────────────────────────────────

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

        # Always update momentum and volatility first (no look-ahead bias)
        self._update_momentum(mid_price)

        # Quote using volatility from *prior* ticks only (no look-ahead bias),
        # then update volatility with the current tick afterwards.
        if self.sigma != 0.0:
            bid_price, ask_price = self.calculate_quotes(mid_price)
        else:
            bid_price = ask_price = None

        self._update_volatility(mid_price, event["local_ts_ms"])

        if bid_price is None and ask_price is None:
            return

        # ── Requote threshold ─────────────────────────────────────────────
        # Skip cancel-replace if price hasn't moved enough — keeps quotes
        # live longer, reduces staleness, and improves survival rate.
        if not self._needs_requote(mid_price):
            return

        # Capture the orders we're about to cancel so fit_quote can treat
        # their still-held locks as already released (cancels travel through
        # the latency queue and may not have drained yet at this instant).
        soon_to_cancel = tuple(self._active_order_ids)
        self._cancel_active_orders()

        bid_qty = 0
        if bid_price is not None:
            bid_qty = self.fit_quote(
                "buy", bid_price, self.order_size,
                exclude_locks_from_orders=soon_to_cancel,
            )
        ask_qty = 0
        if ask_price is not None:
            ask_qty = self.fit_quote(
                "sell", ask_price, self.order_size,
                exclude_locks_from_orders=soon_to_cancel,
            )

        new_active: list[str] = []
        if bid_price is not None and bid_qty > 0:
            bid_id = f"as_bid_{uuid.uuid4().hex[:8]}"
            self.submit_order(bid_price, "buy", bid_qty, order_id=bid_id)
            new_active.append(bid_id)
        if ask_price is not None and ask_qty > 0:
            ask_id = f"as_ask_{uuid.uuid4().hex[:8]}"
            self.submit_order(ask_price, "sell", ask_qty, order_id=ask_id)
            new_active.append(ask_id)

        self._active_order_ids = new_active
        self._last_quoted_mid = mid_price
        self._force_requote = False

    def on_order_fill(self, fill_event) -> None:
        """
        Triggered when an order is executed.
        Updates inventory based on which side was filled.
        Forces an immediate requote on the next tick so the reservation price
        re-skews for the new inventory level without waiting for a price move.
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

        # Force an immediate requote on the next tick to reflect the new inventory
        self._force_requote = True


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
        {"name": "requote_threshold", "label": "Requote Threshold (USD)", "type": "float",
         "default": 1.0, "min": 0.0, "max": 50.0, "step": 0.5,
         "description": "Only cancel-replace quotes when mid-price moves by this many USD. Higher = fewer quote refreshes, lower staleness."},
        {"name": "trend_skew", "label": "Trend Skew", "type": "float",
         "default": 0.5, "min": 0.0, "max": 2.0, "step": 0.1,
         "description": "How aggressively to shift quotes in the direction of the trend. 0 = off, higher = more trend-following."},
    ],
    liquidity_attr="k",
    category="Stochastic Control",
    capital_check={"mode": "clip", "multiplier": 5},  # inventory accumulates without a hard limit — 5× clip provides buffer for build-up
)