from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    """
    Abstract Base Class for all trading strategies.
    Acts as a bridge between Market Data (Person B) and Exchange (Person C).
    """
    def __init__(self):
        # Placeholder for the exchange simulator instance (Person C's engine)
        self.exchange = None

    def connect_exchange(self, exchange):
        """
        Links the strategy to the exchange simulator.
        Used by the Integration Engineer (Person D) during orchestration.
        """
        self.exchange = exchange

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
            
        # This calls Person C's matching engine receive method
        return self.exchange.receive_order(
            price=price, 
            side=side, 
            amount=amount, 
            order_type=order_type
        )