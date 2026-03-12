from abc import ABC, abstractmethod

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
        Calls the exchange's order reception interface.
        """
        if not self.exchange:
            raise ConnectionError("Strategy is not connected to any exchange engine.")
            
        estimated_exchange_ts = self.current_local_ts + self.current_latency_estimate


        # This calls Person C's matching engine receive method
        return self.exchange.receive_order(
            price=price, 
            side=side, 
            amount=amount, 
            order_type=order_type,
            estimated_exchange_ts=estimated_exchange_ts
        )