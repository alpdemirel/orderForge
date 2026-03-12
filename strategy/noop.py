from .base import BaseStrategy

class NoOpStrategy(BaseStrategy):
    """
    A baseline strategy that listens to events but does not trade.
    Required for integration testing before complex strategies are built.
    """
    def on_market_data(self, event):
        pass  # Only listens, does not execute trades

    def on_order_fill(self, fill_event):
        pass  # Receives notifications but does not react