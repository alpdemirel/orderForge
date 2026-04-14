"""Tests for order book depth snapshot and its inclusion in market data."""

from exchange.engine import ExchangeEngine
from exchange.models import Order, Side, TimeInForce, MARKET_OWNER_ID


def _order(oid, side, price, qty, owner=MARKET_OWNER_ID):
    return Order(order_id=oid, side=side, price=price, quantity=qty,
                 owner_id=owner, tif=TimeInForce.GTC)


def test_empty_book_depth():
    engine = ExchangeEngine()
    snap = engine.book.depth_snapshot(levels=5)
    assert snap == {"bids": [], "asks": []}


def test_depth_aggregates_at_same_price():
    engine = ExchangeEngine()
    engine.book.add(_order("b1", Side.BUY, 1000, 100))
    engine.book.add(_order("b2", Side.BUY, 1000, 200))

    snap = engine.book.depth_snapshot(levels=5)
    assert len(snap["bids"]) == 1
    assert snap["bids"][0] == (1000, 300)


def test_depth_sorted_correctly():
    engine = ExchangeEngine()
    engine.book.add(_order("b1", Side.BUY, 990, 100))
    engine.book.add(_order("b2", Side.BUY, 1000, 200))
    engine.book.add(_order("b3", Side.BUY, 980, 50))
    engine.book.add(_order("a1", Side.SELL, 1010, 100))
    engine.book.add(_order("a2", Side.SELL, 1020, 200))
    engine.book.add(_order("a3", Side.SELL, 1030, 50))

    snap = engine.book.depth_snapshot(levels=5)
    # Bids: highest first
    assert [p for p, _ in snap["bids"]] == [1000, 990, 980]
    # Asks: lowest first
    assert [p for p, _ in snap["asks"]] == [1010, 1020, 1030]


def test_depth_limits_to_n_levels():
    engine = ExchangeEngine()
    for i in range(10):
        engine.book.add(_order(f"b{i}", Side.BUY, 1000 - i * 10, 100))

    snap = engine.book.depth_snapshot(levels=3)
    assert len(snap["bids"]) == 3
    assert snap["bids"][0][0] == 1000  # best bid


def test_depth_in_replayer_payload():
    """Replayer includes depth_bids and depth_asks in market data events."""
    from market_data.replayer import Replayer
    from strategy.base import BaseStrategy

    received = []

    class _Capture(BaseStrategy):
        def on_market_data(self, event):
            received.append(event)
        def on_order_fill(self, fill_event):
            pass

    engine = ExchangeEngine()
    # Seed some book depth
    engine.book.add(_order("b1", Side.BUY, 670000, 1000000))
    engine.book.add(_order("b2", Side.BUY, 669900, 2000000))
    engine.book.add(_order("a1", Side.SELL, 670100, 1000000))
    engine.book.add(_order("a2", Side.SELL, 670200, 2000000))

    strat = _Capture()
    replayer = Replayer("dummy", strat, engine=engine)

    # Manually call _emit_market_data
    replayer._emit_market_data(local_ts_ms=1000, exch_ts_ms=1000)

    assert len(received) == 1
    ev = received[0]
    assert "depth_bids" in ev
    assert "depth_asks" in ev
    assert len(ev["depth_bids"]) == 2
    assert len(ev["depth_asks"]) == 2
    # Check float conversion
    assert ev["depth_bids"][0][0] == 670000 / 10  # PRICE_MULTIPLIER = 10
