import pytest
from common.types import MarketEvent
from common.events import OrderBookSnapshot, OrderUpdate
from market_data.normalizer import normalize_event

def test_normalize_snapshot():
    raw_json = {
        "type": "snapshot",
        "product_id": "BTC-USD",
        "bids": [["30000.00", "0.5"]],
        "asks": [["30001.00", "1.0"]]
    }
    event = MarketEvent(local_ts_ns=1000, channel="level2", raw_json=raw_json)
    
    normalized = normalize_event(event)
    
    assert len(normalized) == 1
    assert isinstance(normalized[0], OrderBookSnapshot)
    assert normalized[0].local_ts_ns == 1000
    assert normalized[0].product_id == "BTC-USD"
    assert normalized[0].bids == [["30000.00", "0.5"]]
    assert normalized[0].asks == [["30001.00", "1.0"]]

def test_normalize_l2update():
    raw_json = {
        "type": "l2update",
        "product_id": "BTC-USD",
        "changes": [
            ["buy", "30000.00", "0.20"],
            ["sell", "30002.00", "0.00"]
        ]
    }
    event = MarketEvent(local_ts_ns=2000, channel="level2", raw_json=raw_json)
    
    normalized = normalize_event(event)
    
    assert len(normalized) == 2
    
    assert isinstance(normalized[0], OrderUpdate)
    assert normalized[0].side == "buy"
    assert normalized[0].price == "30000.00"
    assert normalized[0].size == "0.20"
    
    assert isinstance(normalized[1], OrderUpdate)
    assert normalized[1].side == "sell"
    assert normalized[1].price == "30002.00"
    assert normalized[1].size == "0.00"

def test_normalize_ignored_event():
    raw_json = {
        "type": "heartbeat",
        "product_id": "BTC-USD"
    }
    event = MarketEvent(local_ts_ns=3000, channel="heartbeat", raw_json=raw_json)
    
    normalized = normalize_event(event)
    assert len(normalized) == 0
