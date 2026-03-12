import pytest
from common.types import MarketEvent
from market_data.replay import ReplayEngine

def test_replay_engine_play_bitfinex():
    raw_json_1 = [1234, 333, 49000.0, 0.5]
    event1 = MarketEvent(local_ts_ns=2000, exchange_ts_ns=2000, channel="bf_update", raw_json=raw_json_1)
    
    raw_json_2 = [1234, [[111, 50000.0, 1.5]]]
    event2 = MarketEvent(local_ts_ns=1000, exchange_ts_ns=1000, channel="bf_snapshot", raw_json=raw_json_2)

    # Input out of order (event2 is snapshot at t=1000, event1 is update at t=2000)
    input_stream = iter([event1, event2])

    engine = ReplayEngine(buffer_size=10)
    engine.set_speed(0)  # maximum speed, no sleep
    
    # Engine processes and normalizes the stream
    output = list(engine.play(input_stream))
    
    assert len(output) == 2
    
    # First output should be from event2 (t=1000)
    assert output[0].exchange_ts_ns == 1000
    assert getattr(output[0], "order_id", None) == 111

    # Second output should be from event1 (t=2000)
    assert output[1].exchange_ts_ns == 2000
    assert getattr(output[1], "order_id", None) == 333
