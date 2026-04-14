"""
Edge-case tests covering input validation, boundary conditions, and
degenerate inputs across the OrderForge pipeline.
"""

import math
import os
import tempfile

import pytest

from common.types import MarketEvent
from exchange import ExchangeGateway
from exchange.engine import ExchangeEngine
from exchange.models import (
    MARKET_OWNER_ID, PRICE_MULTIPLIER, QUANTITY_MULTIPLIER,
    Order, OrderStatus, Side, TimeInForce,
)
from exchange.market_event_applicator import MarketEventApplicator
from market_data.reader import parse_line, read_capture_file
from market_data.replayer import Replayer
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy
from strategy.base import BaseStrategy


# ── Strategy input validation ────────────────────────────────────────────────


class _MinimalStrategy(BaseStrategy):
    """Concrete strategy for testing base class validation."""

    def on_market_data(self, event):
        pass

    def on_order_fill(self, fill_event):
        pass


def test_submit_order_rejects_zero_price():
    strat = _MinimalStrategy()
    gw = ExchangeGateway()
    strat.connect_exchange(gw)

    with pytest.raises(ValueError, match="price must be positive"):
        strat.submit_order(price=0, side="buy", amount=1.0)


def test_submit_order_rejects_negative_price():
    strat = _MinimalStrategy()
    gw = ExchangeGateway()
    strat.connect_exchange(gw)

    with pytest.raises(ValueError, match="price must be positive"):
        strat.submit_order(price=-100.0, side="buy", amount=1.0)


def test_submit_order_rejects_zero_amount():
    strat = _MinimalStrategy()
    gw = ExchangeGateway()
    strat.connect_exchange(gw)

    with pytest.raises(ValueError, match="amount must be positive"):
        strat.submit_order(price=100.0, side="buy", amount=0)


def test_submit_order_rejects_negative_amount():
    strat = _MinimalStrategy()
    gw = ExchangeGateway()
    strat.connect_exchange(gw)

    with pytest.raises(ValueError, match="amount must be positive"):
        strat.submit_order(price=100.0, side="sell", amount=-0.5)


def test_submit_order_accepts_valid_inputs():
    strat = _MinimalStrategy()
    gw = ExchangeGateway()
    strat.connect_exchange(gw)

    result = strat.submit_order(price=100.0, side="buy", amount=0.01)
    assert result.accepted is True


# ── Engine rejects zero quantity ─────────────────────────────────────────────


def test_engine_rejects_zero_quantity_order():
    engine = ExchangeEngine()
    order = Order(order_id="z1", side=Side.BUY, price=1000, quantity=0)
    result = engine.submit_order(order)

    assert result.accepted is False
    assert result.reason == "quantity_must_be_positive"
    assert order.status == OrderStatus.REJECTED


# ── Avellaneda-Stoikov parameter validation ──────────────────────────────────


def test_as_rejects_zero_risk_aversion():
    with pytest.raises(ValueError, match="risk_aversion.*must be positive"):
        AvellanedaStoikovStrategy(risk_aversion=0.0)


def test_as_rejects_negative_risk_aversion():
    with pytest.raises(ValueError, match="risk_aversion.*must be positive"):
        AvellanedaStoikovStrategy(risk_aversion=-0.1)


def test_as_rejects_zero_session_duration():
    with pytest.raises(ValueError, match="session_duration.*must be positive"):
        AvellanedaStoikovStrategy(risk_aversion=0.1, session_duration=0.0)


def test_as_rejects_negative_session_duration():
    with pytest.raises(ValueError, match="session_duration.*must be positive"):
        AvellanedaStoikovStrategy(risk_aversion=0.1, session_duration=-10.0)


# ── Volatility NaN guard ─────────────────────────────────────────────────────


def test_volatility_nan_does_not_propagate():
    """If a price jump produces a NaN/inf sigma, the old value is kept."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1)

    # Seed with normal values to get a valid sigma
    strat._update_volatility(100.0, 1000)
    strat._update_volatility(101.0, 2000)
    valid_sigma = strat.sigma
    assert valid_sigma > 0
    assert math.isfinite(valid_sigma)

    # Injecting a zero price would cause log(0) → -inf → NaN in sqrt
    # Since prices can't be zero in normal operation, we test by directly
    # checking that the guard works if weighted_sq_sum somehow goes negative
    # or produces inf.  The guard is on the final assignment.
    strat._mid_prices.append(0.0001)  # extreme price drop
    strat._mid_timestamps.append(3000)
    # Force recalculation — the log(0.0001/101) is valid but extreme
    strat._update_volatility(0.0001, 3000)
    # sigma should still be finite
    assert math.isfinite(strat.sigma)


# ── Negative latency guard (replayer) ────────────────────────────────────────


def test_replayer_clamps_negative_latency(tmp_path):
    """When local_ts < exchange_ts, the replayer should clamp exch_ts to local_ts."""
    # Create a capture file where exchange_ts > local_ts (clock skew)
    capture_file = tmp_path / "events.log"
    # local_ts=100ms, exchange_ts=200ms → negative latency
    capture_file.write_text(
        '100000000|200000000|bf_update|[1, 99001, 43000.0, 0.5]\n'
    )

    strat = _MinimalStrategy()
    engine = ExchangeEngine()
    replayer = Replayer(str(capture_file), strat, engine=engine)
    replayer.run()

    # Strategy clock should have local_ts = exch_ts = 100 (clamped)
    assert strat.current_latency_estimate >= 0


# ── Market event applicator: zero/invalid price+qty ──────────────────────────


def test_applicator_skips_zero_price_order():
    engine = ExchangeEngine()
    applicator = MarketEventApplicator(engine)

    # Inject an order with price=0 (which would round to 0 in fixed-point)
    fills = applicator._insert_market_order(
        bitfinex_id=1001,
        float_price=0.0,
        float_amount=1.0,
        side_str="bid",
    )

    assert fills == []
    assert len(engine.book.orders) == 0


def test_applicator_skips_zero_quantity_order():
    engine = ExchangeEngine()
    applicator = MarketEventApplicator(engine)

    fills = applicator._insert_market_order(
        bitfinex_id=1002,
        float_price=43000.0,
        float_amount=0.0,
        side_str="ask",
    )

    assert fills == []
    assert len(engine.book.orders) == 0


# ── Crossing spread in snapshot ──────────────────────────────────────────────


def test_crossing_spread_in_snapshot_does_not_crash():
    """A snapshot where best bid > best ask should not cause errors.

    The replayer skips emitting market data when bid >= ask (line 102 of
    replayer.py), so this exercises the engine's ability to hold a crossed
    book without crashing.
    """
    engine = ExchangeEngine()
    applicator = MarketEventApplicator(engine)

    # Snapshot with bid at 44000 and ask at 43000 (crossed)
    event = MarketEvent(
        local_ts_ns=1_000_000_000,
        channel="bf_snapshot",
        raw_json=[
            12345,
            [
                [1001, 44000.0, 1.0],    # bid at 44000
                [1002, 43000.0, -1.0],   # ask at 43000
            ],
        ],
    )

    # Should not raise
    applicator.apply(event)

    # Both orders should be on the book
    assert len(engine.book.orders) == 2

    # best_bid > best_ask (crossed)
    assert engine.book.best_bid() > engine.book.best_ask()


# ── Empty and heartbeat-only capture files ───────────────────────────────────


def test_replay_empty_file(tmp_path):
    """Replaying an empty file should complete without error."""
    capture_file = tmp_path / "empty.log"
    capture_file.write_text("")

    strat = _MinimalStrategy()
    replayer = Replayer(str(capture_file), strat)
    replayer.run()  # should not raise


def test_replay_heartbeat_only_file(tmp_path):
    """A file with only heartbeats should replay without sending market data."""
    capture_file = tmp_path / "heartbeats.log"
    lines = [
        '1000000000|1000000000|bf_heartbeat|[12345]\n',
        '2000000000|2000000000|bf_heartbeat|[12345]\n',
        '3000000000|3000000000|bf_heartbeat|[12345]\n',
    ]
    capture_file.write_text("".join(lines))

    received_events = []

    class _RecordingStrategy(BaseStrategy):
        def on_market_data(self, event):
            received_events.append(event)

        def on_order_fill(self, fill_event):
            pass

    strat = _RecordingStrategy()
    engine = ExchangeEngine()
    replayer = Replayer(str(capture_file), strat, engine=engine)
    replayer.run()

    # Heartbeats don't update the book → no market data emitted
    assert received_events == []


# ── Reader: malformed and edge-case lines ────────────────────────────────────


def test_parse_line_empty_string():
    assert parse_line("") is None
    assert parse_line("   \n") is None


def test_parse_line_too_few_fields():
    assert parse_line("12345|bf_update", strict=False) is None


def test_parse_line_too_few_fields_strict():
    from market_data.exceptions import MalformedRecordError

    with pytest.raises(MalformedRecordError):
        parse_line("12345|bf_update", strict=True)


def test_read_capture_file_empty(tmp_path):
    f = tmp_path / "empty.log"
    f.write_text("")
    events = list(read_capture_file(str(f)))
    assert events == []


# ── Order book: duplicate order_id ───────────────────────────────────────────


def test_order_book_rejects_duplicate_order_id():
    from exchange.order_book import OrderBook

    book = OrderBook()
    o1 = Order(order_id="dup", side=Side.BUY, price=1000, quantity=100)
    book.add(o1)

    o2 = Order(order_id="dup", side=Side.SELL, price=1010, quantity=100)
    with pytest.raises(ValueError, match="already exists"):
        book.add(o2)


# ── Order book: cancel unknown order ─────────────────────────────────────────


def test_order_book_cancel_unknown_returns_none():
    from exchange.order_book import OrderBook

    book = OrderBook()
    assert book.cancel("nonexistent") is None


# ── Latency queue: cancel inflight with empty heap ───────────────────────────


def test_latency_queue_cancel_inflight_empty_heap():
    from sim.latency_queue import LatencyQueue

    lq = LatencyQueue(submit_fn=lambda o: None, cancel_fn=lambda oid: None)
    assert lq.cancel_inflight("missing") is False


def test_latency_queue_drain_on_empty_heap():
    from sim.latency_queue import LatencyQueue

    lq = LatencyQueue(submit_fn=lambda o: None, cancel_fn=lambda oid: None)
    results = lq.drain(999999)
    assert results == []


# ── Strategy update_clock with zero exchange timestamp ───────────────────────


def test_strategy_clock_zero_exchange_ts():
    """When exchange_ts is 0, latency estimate should use local_ts as fallback."""
    strat = _MinimalStrategy()
    strat.update_clock(exch_ts=0, local_ts=1000)

    # latency = local - exch = 1000 - 0 = 1000
    # This is technically wrong but the replayer handles it by setting
    # exch_ts = local_ts when exchange_ts_ns is 0 (line 61 of replayer.py).
    # This test documents the raw behavior.
    assert strat.current_local_ts == 1000
    assert strat.current_exch_ts == 0
    assert strat.current_latency_estimate == 1000
