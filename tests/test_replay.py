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
