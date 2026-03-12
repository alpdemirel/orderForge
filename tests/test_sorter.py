import pytest
import random
from common.types import MarketEvent
from market_data.sorter import sort_market_events

def test_sorter_dual_timestamps_priority():
    """
    Sorting must prioritize exchange_ts_ns if > 0.
    """
    e1 = MarketEvent(local_ts_ns=2000, exchange_ts_ns=1000, channel="bf_update", raw_json={})
    e2 = MarketEvent(local_ts_ns=1000, exchange_ts_ns=2000, channel="bf_update", raw_json={})
    e3 = MarketEvent(local_ts_ns=1500, exchange_ts_ns=1500, channel="bf_update", raw_json={})

    expected = [e1, e3, e2]
    
    for _ in range(5):
        events = [e1, e2, e3]
        random.shuffle(events)
        assert sort_market_events(events) == expected

def test_sorter_fallback_to_local_ts():
    """
    If exchange_ts_ns is 0 (legacy or heartbeat), it should fallback to local_ts_ns.
    """
    e1 = MarketEvent(local_ts_ns=1000, exchange_ts_ns=0, channel="bf_update", raw_json={})
    e2 = MarketEvent(local_ts_ns=2000, exchange_ts_ns=0, channel="bf_update", raw_json={})
    
    events = [e2, e1]
    assert sort_market_events(events) == [e1, e2]
