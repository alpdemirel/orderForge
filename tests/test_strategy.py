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