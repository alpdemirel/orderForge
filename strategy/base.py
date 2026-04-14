import uuid
from abc import ABC, abstractmethod

from exchange import Order, Side, TimeInForce
from exchange.models import QUANTITY_MULTIPLIER, PRICE_MULTIPLIER

class BaseStrategy(ABC):
    """
    Abstract Base Class for all trading strategies.
    Acts as a bridge between Market Data (Person B) and Exchange (Person C).
    """
    def __init__(self):
        # Placeholder for the exchange simulator instance (Person C's engine)
        self.exchange = None

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
            tif=tif
        )

        # Route through latency queue if attached, otherwise submit directly
        if self._latency_queue is not None:
            landing_ts = self.current_local_ts + self.current_latency_estimate
            self._latency_queue.enqueue_submit(order, landing_ts)
            return None  # result comes later when drain() fires
        return self.exchange.submit_order(order)

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