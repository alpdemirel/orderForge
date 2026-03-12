import pytest
from common.types import MarketEvent
from common.events import OrderUpdate, OrderCancel
from market_data.normalizer import normalize_event

def test_normalize_snapshot():
    # Bitfinex snapshot payload shape from reader
    # [CHAN_ID, [[ORDER_ID, PRICE, AMOUNT], ...]]
    raw_json = [1234, [[111, 50000.0, 1.5], [222, 51000.0, -2.0]]]
    event = MarketEvent(local_ts_ns=100, exchange_ts_ns=50, channel="bf_snapshot", raw_json=raw_json)
    
    norms = normalize_event(event)
    assert len(norms) == 2
    
    assert isinstance(norms[0], OrderUpdate)
    assert norms[0].order_id == 111
    assert norms[0].price == 50000.0
    assert norms[0].side == "bid"
    assert norms[0].amount == 1.5
    
    assert isinstance(norms[1], OrderUpdate)
    assert norms[1].order_id == 222
    assert norms[1].price == 51000.0
    assert norms[1].side == "ask"
    assert norms[1].amount == -2.0

def test_normalize_update():
    # Bitfinex update payload shape from reader (4 elements)
    # [CHAN_ID, ORDER_ID, PRICE, AMOUNT]
    
    # Normal update (add/modify)
    raw_json1 = [1234, 333, 49000.0, 0.5]
    e1 = MarketEvent(local_ts_ns=100, exchange_ts_ns=60, channel="bf_update", raw_json=raw_json1)
    
    norms1 = normalize_event(e1)
    assert len(norms1) == 1
    assert isinstance(norms1[0], OrderUpdate)
    assert norms1[0].order_id == 333
    assert norms1[0].side == "bid"
    
    # Cancellation (price == 0)
    raw_json2 = [1234, 333, 0.0, -1]
    e2 = MarketEvent(local_ts_ns=100, exchange_ts_ns=70, channel="bf_update", raw_json=raw_json2)
    
    norms2 = normalize_event(e2)
    assert len(norms2) == 1
    assert isinstance(norms2[0], OrderCancel)
    assert norms2[0].order_id == 333
