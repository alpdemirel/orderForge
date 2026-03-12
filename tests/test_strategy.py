import pytest
from strategy.noop import NoOpStrategy

def test_noop_strategy_initialization():
    # Can the strategy be initialized without errors?
    strategy = NoOpStrategy()
    assert strategy.exchange is None

def test_unconnected_strategy_raises_error():
    # Does it raise an error if an order is submitted before connecting to the exchange?
    strategy = NoOpStrategy()
    with pytest.raises(ConnectionError):
        strategy.submit_order(price=50000, side="buy", amount=1)

class DummyExchange:
    """
    A mock exchange to verify if orders arrive correctly using the new submit_order interface.
    """
    def __init__(self):
        self.received_orders = []
        
    def submit_order(self, order, estimated_exchange_ts=0):
        # Record the received order object directly
        self.received_orders.append(order)
        return "fake_order_accepted"

def test_latency_calculation_and_order_submission():
    strategy = NoOpStrategy()
    dummy_exchange = DummyExchange()
    strategy.connect_exchange(dummy_exchange)
    
    # 1. Update clock and test latency math
    strategy.update_clock(exch_ts=100, local_ts=110)
    assert strategy.current_latency_estimate == 10
    
    # 2. Submit order using the new integrated method
    strategy.submit_order(price=70000, side="buy", amount=1)
    
    # 3. Verify the order reached the mock exchange successfully
    received_order = dummy_exchange.received_orders[0]
    
    # Assert it translated our intent correctly into an Order object
    assert received_order.price == 70000
    assert received_order.side.name == "BUY"  # Enum check