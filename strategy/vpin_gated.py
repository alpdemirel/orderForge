"""
VPIN-Gated Market Making Strategy
Reference: Flow Toxicity and Liquidity in a High-Frequency World
(Easley, López de Prado & O'Hara, 2012)

A simple constant-spread market maker with a VPIN (Volume-synchronized
Probability of Informed Trading) toxicity filter. When VPIN exceeds a
threshold, order flow is likely informed and the strategy halts quoting
to avoid adverse selection. When VPIN is low, the strategy quotes
symmetrically at a fixed half-spread around the mid-price.

VPIN computation uses Bulk Volume Classification (BVC):
  - Standardize log-return: z = Δlog(mid) / σ_returns
  - Buy fraction = Φ(z), sell fraction = 1 - Φ(z)
  - Each tick contributes unit volume to the current bucket
  - When bucket reaches N ticks, finalize and roll into the window
  - VPIN = average over last K buckets of |buy_vol - sell_vol| / bucket_size

Hard inventory limits stop quoting one side when reached:
  q >=  Q  → no bid posted
  q <= -Q  → no ask posted
"""
import math
import uuid
from collections import deque
from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class VPINGatedStrategy(BaseStrategy):
    """
    Constant-spread MM with VPIN-based toxicity gating.
    """

    _RETURN_WINDOW = 200  # sample size for return σ used in BVC

    def __init__(
        self,
        half_spread: float = 1.0,
        order_size: float = 0.01,
        max_inventory: float = 0.5,
        vpin_threshold: float = 0.4,
        vpin_bucket_ticks: int = 100,
        vpin_window: int = 50,
        maker_fee_bps: int = 10,
    ):
        super().__init__()

        if half_spread <= 0:
            raise ValueError(f"half_spread must be positive, got {half_spread}")
        if order_size <= 0:
            raise ValueError(f"order_size must be positive, got {order_size}")
        if max_inventory <= 0:
            raise ValueError(f"max_inventory must be positive, got {max_inventory}")
        if not 0.0 < vpin_threshold < 1.0:
            raise ValueError(f"vpin_threshold must be in (0, 1), got {vpin_threshold}")
        if vpin_bucket_ticks < 2:
            raise ValueError(f"vpin_bucket_ticks must be >= 2, got {vpin_bucket_ticks}")
        if vpin_window < 1:
            raise ValueError(f"vpin_window must be >= 1, got {vpin_window}")

        self.half_spread = half_spread
        self.order_size = order_size
        self.max_inventory = max_inventory
        self.vpin_threshold = vpin_threshold
        # Cast to int — frontend sends parseFloat() values, deque maxlen needs int
        self.vpin_bucket_ticks = int(vpin_bucket_ticks)
        self.vpin_window = int(vpin_window)
        self._maker_fee_rate = maker_fee_bps / 10_000

        # Inventory
        self.inventory_position = 0.0

        # VPIN state
        self._last_mid: float | None = None
        self._returns: deque = deque(maxlen=self._RETURN_WINDOW)
        self._bucket_buy: float = 0.0
        self._bucket_sell: float = 0.0
        self._bucket_count: int = 0
        self._buckets: deque = deque(maxlen=self.vpin_window)
        self.vpin: float = 0.0  # current VPIN estimate

        # Active orders
        self._active_order_ids: list[str] = []

    # ── VPIN computation ──────────────────────────────────────────────────

    def _update_vpin(self, mid: float) -> None:
        """Update VPIN with the latest mid-price using BVC classification."""
        if self._last_mid is None or self._last_mid <= 0 or mid <= 0:
            self._last_mid = mid
            return

        log_ret = math.log(mid / self._last_mid)
        self._last_mid = mid
        self._returns.append(log_ret)

        # Need enough return samples to estimate σ
        n = len(self._returns)
        if n < 20:
            return
        # Manual stdev — much faster than statistics.stdev (avoids Decimal overhead)
        mean = sum(self._returns) / n
        var = sum((r - mean) ** 2 for r in self._returns) / (n - 1)
        if var <= 0 or not math.isfinite(var):
            return
        sigma = math.sqrt(var)

        # BVC: probability of buy from standardized return
        z = log_ret / sigma
        buy_fraction = _normal_cdf(z)
        sell_fraction = 1.0 - buy_fraction

        # Unit volume per tick (we don't have trade volume, use tick count)
        self._bucket_buy += buy_fraction
        self._bucket_sell += sell_fraction
        self._bucket_count += 1

        if self._bucket_count >= self.vpin_bucket_ticks:
            bucket_size = float(self._bucket_count)
            order_imbalance = abs(self._bucket_buy - self._bucket_sell) / bucket_size
            self._buckets.append(order_imbalance)
            self._bucket_buy = 0.0
            self._bucket_sell = 0.0
            self._bucket_count = 0

            if self._buckets:
                self.vpin = sum(self._buckets) / len(self._buckets)

    # ── Quote logic ───────────────────────────────────────────────────────

    def _cancel_active_orders(self) -> None:
        if self.exchange is None:
            return
        for oid in self._active_order_ids:
            self.cancel_order(oid)
        self._active_order_ids.clear()

    def on_market_data(self, event: dict) -> None:
        mid = (event["best_bid"] + event["best_ask"]) / 2.0

        # Update toxicity estimate
        self._update_vpin(mid)

        # Always cancel stale quotes before re-evaluating
        soon_to_cancel = tuple(self._active_order_ids)
        self._cancel_active_orders()

        # Gate: if flow is toxic, stay flat (no quotes)
        if self.vpin > self.vpin_threshold:
            return

        # Hard inventory limits
        q = self.inventory_position
        Q = self.max_inventory

        bid_price = None if q >= Q else mid - self.half_spread
        ask_price = None if q <= -Q else mid + self.half_spread

        if bid_price is None and ask_price is None:
            return
        if bid_price is not None and bid_price <= 0:
            return

        new_ids: list[str] = []

        if bid_price is not None:
            bid_qty = self.fit_quote(
                "buy", bid_price, self.order_size,
                exclude_locks_from_orders=soon_to_cancel,
            )
            if bid_qty > 0:
                bid_id = f"vpin_bid_{uuid.uuid4().hex[:8]}"
                self.submit_order(bid_price, "buy", bid_qty, order_id=bid_id)
                new_ids.append(bid_id)

        if ask_price is not None:
            ask_qty = self.fit_quote(
                "sell", ask_price, self.order_size,
                exclude_locks_from_orders=soon_to_cancel,
            )
            if ask_qty > 0:
                ask_id = f"vpin_ask_{uuid.uuid4().hex[:8]}"
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
    strategy_id="vpin_gated",
    name="VPIN-Gated MM",
    description="Constant-spread market making with a VPIN (flow toxicity) filter. Halts quoting when order flow is likely informed. (Easley, López de Prado & O'Hara, 2012)",
    cls=VPINGatedStrategy,
    parameters=[
        {"name": "half_spread", "label": "Half Spread (USD)", "type": "float",
         "default": 1.0, "min": 0.1, "max": 100.0, "step": 0.1,
         "description": "Fixed distance from mid-price to each quote in USD."},
        {"name": "order_size", "label": "Order Size (BTC)", "type": "float",
         "default": 0.01, "min": 0.001, "max": 1.0, "step": 0.001,
         "description": "Size of each bid/ask quote in BTC."},
        {"name": "max_inventory", "label": "Max Inventory (BTC)", "type": "float",
         "default": 0.5, "min": 0.01, "max": 10.0, "step": 0.01,
         "description": "Hard inventory limit. Strategy stops quoting one side when this is reached."},
        {"name": "vpin_threshold", "label": "VPIN Threshold", "type": "float",
         "default": 0.4, "min": 0.05, "max": 0.95, "step": 0.05,
         "description": "VPIN level above which the strategy halts quoting. Higher = more permissive."},
        {"name": "vpin_bucket_ticks", "label": "VPIN Bucket Size (ticks)", "type": "int",
         "default": 100, "min": 10, "max": 1000, "step": 10,
         "description": "Number of market data ticks per VPIN bucket. Smaller = faster adaptation."},
        {"name": "vpin_window", "label": "VPIN Window (buckets)", "type": "int",
         "default": 50, "min": 5, "max": 200, "step": 5,
         "description": "Rolling window size in buckets for averaging VPIN."},
    ],
    liquidity_attr="half_spread",
    category="Adverse Selection / Risk",
    capital_check={"mode": "inventory", "param": "max_inventory"},  # hard inventory limit known
)
