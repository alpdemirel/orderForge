import uuid
from abc import ABC, abstractmethod

from exchange import Order, Side, TimeInForce
from exchange.models import QUANTITY_MULTIPLIER, PRICE_MULTIPLIER

class BaseStrategy(ABC):
    """
    Abstract Base Class for all trading strategies.
    Acts as a bridge between Market Data (Person B) and Exchange (Person C).
    """

    # Sizing safeguards for capacity-aware quoting. Subclasses can override.
    # MIN_QUANTITY_BTC: orders smaller than this are dropped to stay above
    # plausible exchange minimum lots and avoid dust quotes.
    # TAKER_FEE_BPS_ESTIMATE: must mirror the engine's TAKER_FEE_BPS so that
    # buy capacity matches the lock formula.
    MIN_QUANTITY_BTC: float = 0.0001
    TAKER_FEE_BPS_ESTIMATE: int = 20

    def __init__(self):
        # Placeholder for the exchange simulator instance (Person C's engine)
        self.exchange = None

        # Account identity used for orders and balance queries.  Defaults to
        # "default" so existing single-strategy setups keep working.
        self.owner_id: str = "default"

        # Optional latency queue — when set, orders/cancels are delayed
        self._latency_queue = None

        # Internal clock and network latency tracking
        self.current_local_ts = 0
        self.current_exch_ts = 0
        self.current_latency_estimate = 0

    def connect_exchange(self, exchange):
        """
        Links the strategy to the exchange simulator.
        Used by the Integration Engineer (Person D) during orchestration.
        """
        self.exchange = exchange

    def connect_latency_queue(self, latency_queue):
        """Attach a LatencyQueue so orders experience simulated network delay."""
        self._latency_queue = latency_queue

    def update_clock(self, exch_ts, local_ts):
        """
        Updates the strategy's internal clock and estimates network latency.
        Must be called every time a new market data event is received.
        Formula: Latency = local_ts - exch_ts
        """
        self.current_local_ts = local_ts
        self.current_exch_ts = exch_ts
        self.current_latency_estimate = local_ts - exch_ts

    @abstractmethod
    def on_market_data(self, event):
        """
        Callback triggered when new market data is replayed from logs.
        Implemented by the specific strategy logic.
        """
        pass

    @abstractmethod
    def on_order_fill(self, fill_event):
        """
        Callback triggered when a submitted order is matched/filled by the exchange.
        """
        pass

    def submit_order(self, price, side, amount, order_type="GTC", order_id=None):
        """
        Gateway method for the strategy to send orders to the exchange.
        Automatically converts strategy-level intent into the engine's native Order object.

        When a latency queue is attached, the order is enqueued with a
        predicted landing timestamp instead of being submitted immediately.
        """
        if not self.exchange:
            raise ConnectionError("Strategy is not connected to any exchange engine.")

        # Convert simple string inputs from the strategy (e.g., "buy") into the exchange's Enum types
        side_enum = Side.BUY if str(side).lower() == "buy" else Side.SELL

        order_type_lower = str(order_type).lower()
        if order_type_lower == "ioc":
            tif = TimeInForce.IOC
        elif order_type_lower == "post_only":
            tif = TimeInForce.POST_ONLY
        else:
            tif = TimeInForce.GTC

        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        if amount <= 0:
            raise ValueError(f"amount must be positive, got {amount}")

        # Convert the human-friendly quantity into the engine's fixed-point integer units using QUANTITY_MULTIPLIER
        quantity = round(amount * QUANTITY_MULTIPLIER)
        price_int = round(price * PRICE_MULTIPLIER)

        # Create a unique ID for each order (or use the provided one)
        if order_id is None:
            order_id = f"strat_order_{uuid.uuid4().hex[:8]}"

        # Prepare the concrete Order object expected by Bora's matching engine
        order = Order(
            order_id=order_id,
            side=side_enum,
            price=price_int,
            quantity=quantity,
            owner_id=self.owner_id,
            tif=tif,
        )

        # Route through latency queue if attached, otherwise submit directly
        if self._latency_queue is not None:
            landing_ts = self.current_local_ts + self.current_latency_estimate
            self._latency_queue.enqueue_submit(order, landing_ts)
            return None  # result comes later when drain() fires
        return self.exchange.submit_order(order)

    # ── Balance / capacity API ────────────────────────────────────────────
    #
    # These are opt-in: when the exchange has not enforced balance for this
    # strategy's owner_id (i.e., no seed_account() call), fit_quote() returns
    # the requested target unchanged.  Strategies can therefore call into
    # this API freely without changing behavior in unconstrained backtests.

    def is_balance_enforced(self) -> bool:
        if self.exchange is None:
            return False
        check = getattr(self.exchange, "is_balance_enforced", None)
        return bool(check and check(self.owner_id))

    def available_quote_usd(self) -> float:
        """Available cash for new BUY orders, in USD (float)."""
        if self.exchange is None:
            return float("inf")
        state = self.exchange.get_account_state(self.owner_id)
        fp_divisor = PRICE_MULTIPLIER * QUANTITY_MULTIPLIER
        return state.available_quote() / fp_divisor

    def available_base_btc(self) -> float:
        """Available base inventory for new SELL orders, in BTC (float)."""
        if self.exchange is None:
            return float("inf")
        state = self.exchange.get_account_state(self.owner_id)
        return state.available_base() / QUANTITY_MULTIPLIER

    def fit_quote(
        self,
        side,
        price: float,
        target_amount: float,
        *,
        exclude_locks_from_orders: tuple[str, ...] = (),
    ) -> float:
        """Return the largest amount (BTC) that can actually be quoted.

        Capacity comes from the seeded account's available balance, mirroring
        the engine's lock formula so a successful fit is also accepted at
        submit time.  Returns 0.0 when capacity falls below MIN_QUANTITY_BTC.

        ``exclude_locks_from_orders`` lets a caller treat specific orders'
        existing locks as already released — useful in a cancel-then-replace
        cycle where cancels haven't yet drained through the latency queue,
        so the engine still reports those orders as locking funds.
        """
        if price <= 0 or target_amount <= 0:
            return 0.0

        if not self.is_balance_enforced():
            return target_amount  # unconstrained — honor user's target

        state = self.exchange.get_account_state(self.owner_id)

        extra_quote = 0
        extra_base = 0
        for oid in exclude_locks_from_orders:
            existing = self.exchange.get_order(oid)
            if existing is None:
                continue
            extra_quote += existing.locked_quote
            extra_base += existing.locked_base

        side_str = str(side).lower()
        if side_str == "buy":
            effective_quote_fp = state.available_quote() + extra_quote
            if effective_quote_fp <= 0:
                return 0.0
            # Integer-exact inverse of the engine's lock formula:
            #   lock_fp = price_fp * qty_fp * (10_000 + TAKER_FEE_BPS) // 10_000
            # Floor division guarantees the resulting qty cannot lock more
            # than effective_quote_fp once submit_order rounds back to int.
            price_fp = round(price * PRICE_MULTIPLIER)
            if price_fp <= 0:
                return 0.0
            denom = price_fp * (10_000 + self.TAKER_FEE_BPS_ESTIMATE)
            max_qty_fp = (effective_quote_fp * 10_000) // denom
            max_qty = max_qty_fp / QUANTITY_MULTIPLIER
        else:
            effective_base_fp = state.available_base() + extra_base
            if effective_base_fp <= 0:
                return 0.0
            max_qty = effective_base_fp / QUANTITY_MULTIPLIER

        capacity = min(target_amount, max_qty)
        if capacity < self.MIN_QUANTITY_BTC:
            return 0.0
        return capacity

    def cancel_order(self, order_id: str):
        """Cancel an order, routing through the latency queue if attached."""
        if not self.exchange:
            raise ConnectionError("Strategy is not connected to any exchange engine.")

        if self._latency_queue is not None:
            # Try to cancel locally first (order hasn't left yet)
            if self._latency_queue.cancel_inflight(order_id):
                return True
            # Otherwise enqueue a cancel that will land after network delay
            landing_ts = self.current_local_ts + self.current_latency_estimate
            self._latency_queue.enqueue_cancel(order_id, landing_ts)
            return None  # async cancel
        return self.exchange.cancel_order(order_id)