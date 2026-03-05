import pytest
from common.types import MarketEvent
from market_data.sorter import sort_market_events
from market_data.replay import ReplayEngine
import random

def test_sorter_deterministic():
    """
    Tests that identical timestamps are securely tie-broken by sequence ID 
    and channel, ensuring deterministic identical outputs across shuffles.
    """
    event1 = MarketEvent(local_ts_ns=1000, channel="level2", raw_json={"sequence": 1})
    event2 = MarketEvent(local_ts_ns=1000, channel="level2", raw_json={"sequence": 2})
    event3 = MarketEvent(local_ts_ns=1000, channel="full", raw_json={"sequence": 2})
    event4 = MarketEvent(local_ts_ns=2000, channel="heartbeat", raw_json={})

    # Expected sorted order
    expected = [event1, event3, event2, event4]

    # Determinism check across random shuffles
    for _ in range(5):
        events = [event1, event2, event3, event4]
        random.shuffle(events)
        
        sorted_res = sort_market_events(events)
        assert sorted_res == expected

def test_replay_engine_buffering():
    event1 = MarketEvent(local_ts_ns=2000, channel="level2", raw_json={})
    event2 = MarketEvent(local_ts_ns=1000, channel="level2", raw_json={})
    event3 = MarketEvent(local_ts_ns=3000, channel="level2", raw_json={})
    
    # Input out of order
    input_stream = iter([event1, event2, event3])
    
    engine = ReplayEngine(buffer_size=10)
    output = list(engine.process(input_stream))
    
    # Assert they are yielded out strictly in time order
    assert len(output) == 3
    assert output[0].local_ts_ns == 1000
    assert output[1].local_ts_ns == 2000
    assert output[2].local_ts_ns == 3000

def test_replay_engine_play():
    event1 = MarketEvent(local_ts_ns=1000, channel="level2", raw_json={
        "type": "l2update",
        "product_id": "BTC-USD",
        "changes": [["buy", "100.0", "1.0"]]
    })
    event2 = MarketEvent(local_ts_ns=2000, channel="level2", raw_json={
        "type": "l2update",
        "product_id": "BTC-USD",
        "changes": [["sell", "101.0", "2.0"]]
    })

    input_stream = iter([event2, event1])

    engine = ReplayEngine(buffer_size=10)
    engine.set_speed(0)  # maximum speed, no sleep
    
    output = list(engine.play(input_stream))
    
    assert len(output) == 2
    assert output[0].local_ts_ns == 1000
    assert getattr(output[0], "side", None) == "buy"
    assert getattr(output[0], "price", None) == "100.0"

    assert output[1].local_ts_ns == 2000
    assert getattr(output[1], "side", None) == "sell"
    assert getattr(output[1], "price", None) == "101.0"
