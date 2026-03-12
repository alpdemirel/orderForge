import pytest
from strategy.noop import NoOpStrategy
from exchange.engine import ExchangeEngine
from exchange.models import OrderStatus

def test_latency_simulation_queue():
    """
    Test that the strategy calculates latency correctly and that the exchange
    queues the order until the target timestamp is reached.
    """
    engine = ExchangeEngine()
    strategy = NoOpStrategy()
    strategy.connect_exchange(engine)

    # 1. Provide a market data update (Exchange time 100, local time 110)
    # The strategy estimates a one-way latency of 10ms.
    strategy.update_clock(exch_ts=100, local_ts=110)
    assert strategy.current_latency_estimate == 10

    # 2. Strategy submits an order
    # It sends the order at local_ts=110, expecting it to land at 110 + 10 = 120ms.
    strategy.submit_order(price=70000, side="buy", amount=1)

    # 3. Verify the order is IN THE QUEUE but NOT YET EXECUTED
    # Get the order id from the queue
    queued_event = engine.event_queue[0]
    assert queued_event.target_ts_ns == 120
    order = queued_event.args[0]
    assert order.status == OrderStatus.OPEN
    
    # 4. Advance time to 119ms
    # Time hasn't reached the target_ts, so the order shouldn't execute
    engine.tick(119)
    assert len(engine.event_queue) == 1
    assert engine.get_order(order.order_id) is None # Not yet in the exchange's master order map or book
    
    # 5. Advance time to 120ms (Landing time)
    # The order should process and its status will be updated to PARTIALLY_FILLED or FILLED or restful OPEN.
    # In this case, an empty book means a GTC order rests in the book.
    engine.tick(120)
    
    assert len(engine.event_queue) == 0
    assert engine.get_order(order.order_id) is not None
    assert engine.book.orders.get(order.order_id) is not None
