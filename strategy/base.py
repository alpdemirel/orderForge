import uuid
from abc import ABC, abstractmethod

from exchange import Order, Side, TimeInForce
from exchange.models import QUANTITY_MULTIPLIER

class BaseStrategy(ABC):
    """
    Abstract Base Class for all trading strategies.
    Acts as a bridge between Market Data (Person B) and Exchange (Person C).
    """
    def __init__(self):
        # Placeholder for the exchange simulator instance (Person C's engine)
        self.exchange = None

        # Internal clock and network latency tracking
        self.current_local_ts = 0
        self.current_latency_estimate = 0

    def connect_exchange(self, exchange):
        """
        Links the strategy to the exchange simulator.
        Used by the Integration Engineer (Person D) during orchestration.
        """
        self.exchange = exchange

    def update_clock(self, exch_ts, local_ts):
        """
        Updates the strategy's internal clock and estimates network latency.
        Must be called every time a new market data event is received.
        Formula: Latency = local_ts - exch_ts
        """
        self.current_local_ts = local_ts
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

    def submit_order(self, price, side, amount, order_type="GTC"):
        """
        Gateway method for the strategy to send orders to the exchange.
        Automatically converts strategy-level intent into the engine's native Order object.
        """
        if not self.exchange:
            raise ConnectionError("Strategy is not connected to any exchange engine.")
            
        # Our latency math is still preserved here
        estimated_exchange_ts = self.current_local_ts + self.current_latency_estimate

        # Convert simple string inputs from the strategy (e.g., "buy") into the exchange's Enum types
        side_enum = Side.BUY if str(side).lower() == "buy" else Side.SELL

        order_type_lower = str(order_type).lower()
        if order_type_lower == "ioc":
            tif = TimeInForce.IOC
        elif order_type_lower == "post_only":
            tif = TimeInForce.POST_ONLY
        else:
            tif = TimeInForce.GTC

        # Convert the human-friendly quantity into the engine's fixed-point integer units using QUANTITY_MULTIPLIER
        quantity = int(amount * QUANTITY_MULTIPLIER)
        
        # Create a unique ID for each order
        order_id = f"strat_order_{uuid.uuid4().hex[:8]}"

        # Prepare the concrete Order object expected by Bora's matching engine
        order = Order(
            order_id=order_id,
            side=side_enum,
            price=price,
            quantity=quantity,
            tif=tif
        )

        # Call the correct function on the exchange adapter/gateway
        # Send the `estimated_exchange_ts` so the engine can land this order accurately in the future
        return self.exchange.submit_order(order, estimated_exchange_ts=estimated_exchange_ts)