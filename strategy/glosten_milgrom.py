"""
Glosten-Milgrom (1985) Sequential Trade Model

Reference: Bid, Ask, and Transaction Prices in a Specialist Market with
Heterogeneously Informed Traders (Glosten & Milgrom, 1985).

The dealer faces two kinds of counterparties:

  * a fraction ``alpha`` of informed traders who know the true value
    ``V`` in {V_H, V_L} and always trade in the direction of their
    private information;
  * a fraction ``1 - alpha`` of noise traders who buy or sell with
    equal probability.

The dealer maintains a Bayesian belief ``delta = P(V = V_H)`` and
updates it after every observed market trade.  Conditional arrival
probabilities:

    P(Buy  | V = V_H) = alpha + (1 - alpha) / 2 = (1 + alpha) / 2
    P(Buy  | V = V_L) =            (1 - alpha) / 2
    P(Sell | V = V_H) =            (1 - alpha) / 2
    P(Sell | V = V_L) = alpha + (1 - alpha) / 2 = (1 + alpha) / 2

Bayes updates (derived from the above):

    delta | Buy  = delta * (1 + alpha) /
                   [delta * (1 + alpha) + (1 - delta) * (1 - alpha)]
    delta | Sell = delta * (1 - alpha) /
                   [delta * (1 - alpha) + (1 - delta) * (1 + alpha)]

Zero-profit quotes (the dealer breaks even conditional on the side of
the next arriving order):

    ask = E[V | next trade is a Buy]
        = V_H * d_post_buy  + V_L * (1 - d_post_buy)
    bid = E[V | next trade is a Sell]
        = V_H * d_post_sell + V_L * (1 - d_post_sell)

The resulting ``ask - bid`` spread captures the adverse-selection
cost.  With neutral belief (delta = 0.5) a larger informed fraction
widens the spread; asymmetric belief skews both quotes in the same
direction.

Adapting the classical discrete model to a continuous LOB requires
two implementation choices:

  1.  ``V_H`` / ``V_L`` are unobservable.  We anchor them at the
      current mid price plus / minus ``value_half_range_bps``
      (basis points).  Each tick re-anchors on the new mid.
  2.  Under a long trend the belief would saturate at 0 or 1.  A small
      per-tick ``belief_decay`` pulls it back toward 0.5 to keep the
      model responsive.
"""

import math
import uuid

from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


class GlostenMilgromStrategy(BaseStrategy):
    """Sequential-trade Bayesian market making strategy."""

    def __init__(
        self,
        informed_fraction: float = 0.3,
        value_half_range_bps: float = 10.0,
        belief_decay: float = 0.05,
        order_size: float = 0.01,
        session_duration: float = 86400.0,
        maker_fee_bps: int = 10,
    ):
        super().__init__()

        if not 0.0 < informed_fraction < 1.0:
            raise ValueError(
                f"informed_fraction must be in (0, 1), got {informed_fraction}"
            )
        if value_half_range_bps <= 0:
            raise ValueError(
                f"value_half_range_bps must be positive, got {value_half_range_bps}"
            )
        if not 0.0 <= belief_decay < 1.0:
            raise ValueError(
                f"belief_decay must be in [0, 1), got {belief_decay}"
            )
        if order_size <= 0:
            raise ValueError(f"order_size must be positive, got {order_size}")
        if session_duration <= 0:
            raise ValueError(
                f"session_duration must be positive, got {session_duration}"
            )

        self.alpha: float = informed_fraction
        self.value_half_range: float = value_half_range_bps / 10_000.0
        self.belief_decay: float = belief_decay
        self.order_size: float = order_size
        self._maker_fee_rate: float = maker_fee_bps / 10_000.0

        # Bayesian belief that V = V_H.  Neutral prior.
        self.belief: float = 0.5

        # Inventory state (base currency — BTC).
        self.inventory_position: float = 0.0

        # Simulation-clock anchors for session progress diagnostics.
        self._session_start_ms: float | None = None
        self._session_duration_ms: float = session_duration * 1000.0

        # Last observed mid, exposed for diagnostics.
        self._last_mid: float | None = None

        # Active quote tracking for cancel-replace.
        self._active_order_ids: list[str] = []

        # Diagnostic counter — how many trade-driven belief updates.
        self._belief_updates: int = 0

    # ── Belief-update helpers ────────────────────────────────────────────

    def _bayes_update_buy(self, delta: float) -> float:
        num = delta * (1.0 + self.alpha)
        den = num + (1.0 - delta) * (1.0 - self.alpha)
        if den <= 0.0:
            return delta
        return num / den

    def _bayes_update_sell(self, delta: float) -> float:
        num = delta * (1.0 - self.alpha)
        den = num + (1.0 - delta) * (1.0 + self.alpha)
        if den <= 0.0:
            return delta
        return num / den

    def _clamp_belief(self, delta: float) -> float:
        return min(max(delta, 1e-4), 1.0 - 1e-4)

    def _decay_belief(self) -> None:
        """Pull belief softly back toward 0.5 to avoid saturation."""
        self.belief = (1.0 - self.belief_decay) * self.belief + self.belief_decay * 0.5
        self.belief = self._clamp_belief(self.belief)

    # ── Quote computation ────────────────────────────────────────────────

    def compute_quotes(self, mid_price: float) -> tuple[float, float]:
        """
        Return ``(bid_price, ask_price)`` using the current belief state.

        V_H / V_L are anchored at ``mid * (1 +/- value_half_range)``.
        """
        v_high = mid_price * (1.0 + self.value_half_range)
        v_low = mid_price * (1.0 - self.value_half_range)

        delta_post_buy = self._bayes_update_buy(self.belief)
        delta_post_sell = self._bayes_update_sell(self.belief)

        ask = delta_post_buy * v_high + (1.0 - delta_post_buy) * v_low
        bid = delta_post_sell * v_high + (1.0 - delta_post_sell) * v_low

        # Numerical guard — keep bid < mid < ask even at boundaries.
        if bid >= ask:
            eps = max(mid_price * 1e-6, 1e-4)
            bid = min(bid, mid_price - eps)
            ask = max(ask, mid_price + eps)
        return bid, ask

    # ── Pipeline helpers ─────────────────────────────────────────────────

    def _cancel_active_orders(self) -> None:
        if self.exchange is None:
            return
        for oid in self._active_order_ids:
            self.cancel_order(oid)
        self._active_order_ids.clear()

    # ── Event handlers ───────────────────────────────────────────────────

    def on_market_data(self, event: dict) -> None:
        """
        Called on every market-data tick by the replayer.
        Decays belief, recomputes quotes, and issues a cancel/replace cycle.
        """
        if self._session_start_ms is None:
            self._session_start_ms = event["local_ts_ms"]

        mid = (event["best_bid"] + event["best_ask"]) / 2.0
        self._last_mid = mid

        # Belief drifts toward neutral slightly each tick.
        self._decay_belief()

        bid_price, ask_price = self.compute_quotes(mid)

        self._cancel_active_orders()

        bid_id = f"gm_bid_{uuid.uuid4().hex[:8]}"
        ask_id = f"gm_ask_{uuid.uuid4().hex[:8]}"

        self.submit_order(bid_price, "buy", self.order_size, order_id=bid_id)
        self.submit_order(ask_price, "sell", self.order_size, order_id=ask_id)

        self._active_order_ids = [bid_id, ask_id]

    def on_order_fill(self, fill_event) -> None:
        """
        Two responsibilities on every observed fill:

        1.  Update the Bayesian belief based on the *taker* direction of
            the trade.  Every market trade (including trades we are not
            a party to) is informative under the Glosten-Milgrom model.
        2.  Update our inventory position if we were on either side of
            this specific fill.
        """
        # 1. Belief update — driven by taker side of the trade.
        taker_side = fill_event.taker_side
        if taker_side == Side.BUY:
            self.belief = self._bayes_update_buy(self.belief)
        elif taker_side == Side.SELL:
            self.belief = self._bayes_update_sell(self.belief)
        self.belief = self._clamp_belief(self.belief)
        self._belief_updates += 1

        # 2. Inventory update — only if this was our fill.
        if fill_event.maker_owner_id == "default":
            our_side = fill_event.maker_side
        elif fill_event.taker_owner_id == "default":
            our_side = fill_event.taker_side
        else:
            return

        filled_amount = fill_event.quantity / QUANTITY_MULTIPLIER
        if our_side == Side.BUY:
            self.inventory_position += filled_amount
        else:
            self.inventory_position -= filled_amount
