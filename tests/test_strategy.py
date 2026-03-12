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
    A mock exchange to verify if orders arrive with the 
    correctly calculated estimated_exchange_ts.
    """
    def __init__(self):
        self.received_orders = []
        
    def receive_order(self, price, side, amount, order_type, estimated_exchange_ts):
        # Record the received order to verify its parameters in the test
        self.received_orders.append({
            "estimated_exchange_ts": estimated_exchange_ts
        })
        return "fake_order_123"

def test_latency_calculation_and_order_submission():
    # 1. Initialize the strategy and the mock exchange, then connect them
    strategy = NoOpStrategy()
    dummy_exchange = DummyExchange()
    strategy.connect_exchange(dummy_exchange)
    
    # 2. Simulate the exact example from the architecture design (Bora's message):
    # Exchange time (exch_ts) = 100, Local time (local_ts) = 110
    strategy.update_clock(exch_ts=100, local_ts=110)
    
    # 3. Verify that the latency is calculated correctly (110 - 100 = 10)
    assert strategy.current_latency_estimate == 10
    
    # 4. Submit an order
    # (Current local time is 110. Estimated latency is 10. Landing time should be 120)
    strategy.submit_order(price=70000, side="buy", amount=1)
    
    # 5. Inspect the order received by the mock exchange: Is the landing time exactly 120?
    received_order = dummy_exchange.received_orders[0]
    assert received_order["estimated_exchange_ts"] == 120