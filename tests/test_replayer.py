"""Unit tests for the Replayer orchestrator."""

from unittest.mock import MagicMock, call, patch
from common.types import MarketEvent
from exchange.engine import ExchangeEngine
from exchange.models import PRICE_MULTIPLIER
from market_data.replayer import Replayer


@patch("market_data.replayer.read_capture_file")
def test_replayer_emits_market_data(mock_read_capture_file):
    # 1. Prepare a fake Bitfinex snapshot event
    # 1000000000 ns = 1000 ms. 900000000 ns = 900 ms.
    fake_snapshot = MarketEvent(
        local_ts_ns=1000000000,
        exchange_ts_ns=900000000,
        channel="bf_snapshot",
        raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],  # Bid: 60000, Ask: 60005
    )

    mock_read_capture_file.return_value = [fake_snapshot]

    # 2. Initialize Replayer with a mock strategy *and* a real engine
    mock_strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer(filepath="dummy_path.log", strategy=mock_strategy, engine=engine)

    # 3. Run the orchestrator
    replayer.run()

    # 4. ASSERTIONS
    # A. Clock synchronised correctly (ns → ms)
    mock_strategy.update_clock.assert_called_once_with(900, 1000)

    # B. Top-of-book derived from the *engine* book (fixed-point → float)
    mock_strategy.on_market_data.assert_called_once()
    actual_payload = mock_strategy.on_market_data.call_args[0][0]
    assert actual_payload["type"] == "top_of_book"
    assert actual_payload["best_bid"] == round(60000 * PRICE_MULTIPLIER) / PRICE_MULTIPLIER
    assert actual_payload["best_ask"] == round(60005 * PRICE_MULTIPLIER) / PRICE_MULTIPLIER
    assert actual_payload["local_ts_ms"] == 1000
    assert actual_payload["exchange_ts_ms"] == 900
    assert "depth_bids" in actual_payload
    assert "depth_asks" in actual_payload


# ── Synthetic latency tests ─────────────────────────────────────────────────


@patch("market_data.replayer.read_capture_file")
def test_zero_exchange_ts_without_default_latency(mock_read):
    """When exchange_ts is 0 and no default_latency_ms, exch_ts = local_ts
    (zero latency — the old behavior)."""
    event = MarketEvent(
        local_ts_ns=1_000_000_000,  # 1000ms
        exchange_ts_ns=0,
        channel="bf_snapshot",
        raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],
    )
    mock_read.return_value = [event]

    strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer("dummy.log", strategy, engine=engine, default_latency_ms=0)
    replayer.run()

    # exch_ts should equal local_ts → latency = 0
    strategy.update_clock.assert_called_once_with(1000, 1000)


@patch("market_data.replayer.read_capture_file")
def test_zero_exchange_ts_with_default_latency(mock_read):
    """When exchange_ts is 0 and default_latency_ms=50, exch_ts should be
    synthesised as local_ts - 50, giving a 50ms latency estimate."""
    event = MarketEvent(
        local_ts_ns=1_000_000_000,  # 1000ms
        exchange_ts_ns=0,
        channel="bf_snapshot",
        raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],
    )
    mock_read.return_value = [event]

    strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer("dummy.log", strategy, engine=engine, default_latency_ms=50)
    replayer.run()

    # exch_ts = local_ts - 50 = 950
    strategy.update_clock.assert_called_once_with(950, 1000)


@patch("market_data.replayer.read_capture_file")
def test_real_exchange_ts_ignores_default_latency(mock_read):
    """When exchange_ts is present, default_latency_ms is ignored."""
    event = MarketEvent(
        local_ts_ns=1_000_000_000,   # 1000ms
        exchange_ts_ns=900_000_000,  # 900ms → real 100ms latency
        channel="bf_snapshot",
        raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],
    )
    mock_read.return_value = [event]

    strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer("dummy.log", strategy, engine=engine, default_latency_ms=50)
    replayer.run()

    # Should use real exchange_ts, not synthetic
    strategy.update_clock.assert_called_once_with(900, 1000)


@patch("market_data.replayer.read_capture_file")
def test_synthetic_latency_activates_latency_queue(mock_read):
    """With default_latency_ms=50, strategy orders should land 50ms later,
    not on the next tick."""
    # Two events 10ms apart — with 50ms latency, an order placed on
    # event 1 should NOT drain on event 2 (only 10ms later, need 50ms)
    events = [
        MarketEvent(
            local_ts_ns=1_000_000_000, exchange_ts_ns=0,
            channel="bf_snapshot",
            raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],
        ),
        MarketEvent(
            local_ts_ns=1_010_000_000, exchange_ts_ns=0,  # 10ms later
            channel="bf_update",
            raw_json=[1, 111, 60001.0, 1.0],
        ),
        MarketEvent(
            local_ts_ns=1_060_000_000, exchange_ts_ns=0,  # 60ms after first
            channel="bf_update",
            raw_json=[1, 111, 60002.0, 1.0],
        ),
    ]
    mock_read.return_value = events

    # Track drain calls
    from sim.latency_queue import LatencyQueue
    drain_calls = []
    real_queue = LatencyQueue(submit_fn=MagicMock(), cancel_fn=MagicMock())
    original_drain = real_queue.drain

    def tracking_drain(ts):
        drain_calls.append(ts)
        return original_drain(ts)

    real_queue.drain = tracking_drain

    strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer(
        "dummy.log", strategy, engine=engine,
        latency_queue=real_queue, default_latency_ms=50,
    )
    replayer.run()

    # Drain is called with exch_ts = local_ts - 50
    # Event 1: drain(950), Event 2: drain(960), Event 3: drain(1010)
    assert drain_calls == [950, 960, 1010]


# ── Real exchange timestamp latency tests ──────────────────────────────────


@patch("market_data.replayer.read_capture_file")
def test_real_exchange_ts_landing_time(mock_read):
    """Verify the plan's example: exch_ts=100, local_ts=110 → latency=10ms
    → order should land at exch_ts=120ms.

    Three events with real exchange timestamps:
      Event 1: exch=100, local=110 (latency 10ms) — strategy places order
      Event 2: exch=115, local=125 — too early, order lands at 120
      Event 3: exch=125, local=135 — order has landed (120 ≤ 125)
    """
    events = [
        MarketEvent(
            local_ts_ns=110_000_000, exchange_ts_ns=100_000_000,
            channel="bf_snapshot",
            raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],
        ),
        MarketEvent(
            local_ts_ns=125_000_000, exchange_ts_ns=115_000_000,
            channel="bf_update",
            raw_json=[1, 111, 60001.0, 1.0],
        ),
        MarketEvent(
            local_ts_ns=135_000_000, exchange_ts_ns=125_000_000,
            channel="bf_update",
            raw_json=[1, 111, 60002.0, 1.0],
        ),
    ]
    mock_read.return_value = events

    from sim.latency_queue import LatencyQueue
    submitted_orders = []

    def track_submit(order):
        submitted_orders.append(order)
        return MagicMock()

    lq = LatencyQueue(submit_fn=track_submit, cancel_fn=MagicMock())

    strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer(
        "dummy.log", strategy, engine=engine,
        latency_queue=lq, default_latency_ms=50,
    )
    replayer.run()

    # After event 1: strategy sees latency=10ms (110-100)
    strategy.update_clock.assert_any_call(100, 110)

    # Simulate what the strategy would do: enqueue an order at landing_ts=120
    # (local_ts 110 + latency 10 = 120)
    from exchange.models import Order, Side
    test_order = Order(order_id="test1", side=Side.BUY, price=600000, quantity=100000000)
    lq.enqueue_submit(test_order, landing_ts_ms=120)  # 110 + 10

    # Drain at exch_ts=115 — should NOT land yet
    result = lq.drain(115)
    assert len(submitted_orders) == 0, "Order should not land at exch_ts=115"

    # Drain at exch_ts=120 — should land now
    result = lq.drain(120)
    assert len(submitted_orders) == 1, "Order should land at exch_ts=120"
    assert submitted_orders[0].order_id == "test1"


@patch("market_data.replayer.read_capture_file")
def test_real_exchange_ts_varying_latency(mock_read):
    """With real exchange timestamps, latency varies per tick.
    Event 1: latency = 10ms, Event 2: latency = 15ms, Event 3: latency = 8ms.
    Strategy should see different latency estimates on each tick."""
    events = [
        MarketEvent(
            local_ts_ns=110_000_000, exchange_ts_ns=100_000_000,  # latency 10ms
            channel="bf_snapshot",
            raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]],
        ),
        MarketEvent(
            local_ts_ns=225_000_000, exchange_ts_ns=210_000_000,  # latency 15ms
            channel="bf_update",
            raw_json=[1, 111, 60001.0, 1.0],
        ),
        MarketEvent(
            local_ts_ns=338_000_000, exchange_ts_ns=330_000_000,  # latency 8ms
            channel="bf_update",
            raw_json=[1, 111, 60002.0, 1.0],
        ),
    ]
    mock_read.return_value = events

    strategy = MagicMock()
    engine = ExchangeEngine()
    replayer = Replayer("dummy.log", strategy, engine=engine)
    replayer.run()

    # Strategy should see varying latency per tick
    calls = strategy.update_clock.call_args_list
    assert calls[0] == call(100, 110)   # latency 10ms
    assert calls[1] == call(210, 225)   # latency 15ms
    assert calls[2] == call(330, 338)   # latency 8ms