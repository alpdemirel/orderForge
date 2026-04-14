"""Tests for sim.latency_queue — verifies order delay, drain, and cancel mechanics."""

from exchange.models import Order, Side, TimeInForce, ExecutionResult
from sim.latency_queue import LatencyQueue


def _make_order(order_id="o1", price=1000, quantity=100, side=Side.BUY):
    return Order(order_id=order_id, side=side, price=price, quantity=quantity)


class _MockExchange:
    """Minimal exchange stub that records submit/cancel calls."""

    def __init__(self):
        self.submitted: list[Order] = []
        self.cancelled: list[str] = []

    def submit_order(self, order: Order) -> ExecutionResult:
        self.submitted.append(order)
        return ExecutionResult(accepted=True)

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True


# ── Basic enqueue / drain ────────────────────────────────────────────────────


def test_order_not_delivered_before_landing_time():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_submit(_make_order("o1"), landing_ts_ms=100)
    lq.drain(99)

    assert len(exch.submitted) == 0
    assert lq.pending_count() == 1


def test_order_delivered_at_landing_time():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_submit(_make_order("o1"), landing_ts_ms=100)
    results = lq.drain(100)

    assert len(exch.submitted) == 1
    assert exch.submitted[0].order_id == "o1"
    assert len(results) == 1
    assert lq.pending_count() == 0


def test_order_delivered_after_landing_time():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_submit(_make_order("o1"), landing_ts_ms=100)
    lq.drain(200)

    assert len(exch.submitted) == 1


def test_multiple_orders_drain_in_time_order():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_submit(_make_order("late"), landing_ts_ms=200)
    lq.enqueue_submit(_make_order("early"), landing_ts_ms=100)
    lq.enqueue_submit(_make_order("mid"), landing_ts_ms=150)

    lq.drain(150)
    assert [o.order_id for o in exch.submitted] == ["early", "mid"]
    assert lq.pending_count() == 1

    lq.drain(200)
    assert [o.order_id for o in exch.submitted] == ["early", "mid", "late"]


def test_drain_returns_execution_results():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_submit(_make_order("o1"), landing_ts_ms=10)
    lq.enqueue_submit(_make_order("o2"), landing_ts_ms=20)

    results = lq.drain(20)
    assert len(results) == 2
    assert all(r.accepted for r in results)


# ── Cancel enqueue ───────────────────────────────────────────────────────────


def test_cancel_delivered_after_delay():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_cancel("o1", landing_ts_ms=100)
    lq.drain(99)
    assert len(exch.cancelled) == 0

    lq.drain(100)
    assert exch.cancelled == ["o1"]


def test_cancel_does_not_produce_results():
    """drain() only returns results from submits, not cancels."""
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_cancel("o1", landing_ts_ms=10)
    results = lq.drain(10)
    assert results == []


# ── Inflight cancel (local fast-path) ───────────────────────────────────────


def test_cancel_inflight_removes_pending_submit():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_submit(_make_order("o1"), landing_ts_ms=100)
    assert lq.cancel_inflight("o1") is True
    assert lq.pending_count() == 0

    lq.drain(200)
    assert len(exch.submitted) == 0


def test_cancel_inflight_returns_false_for_unknown():
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    assert lq.cancel_inflight("missing") is False


def test_cancel_inflight_does_not_remove_pending_cancel():
    """cancel_inflight only removes pending submits, not pending cancels."""
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    lq.enqueue_cancel("o1", landing_ts_ms=100)
    assert lq.cancel_inflight("o1") is False
    assert lq.pending_count() == 1


# ── Sequence tiebreaker ─────────────────────────────────────────────────────


def test_same_landing_time_preserves_insertion_order():
    """Orders with identical landing times drain in FIFO order."""
    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    for i in range(5):
        lq.enqueue_submit(_make_order(f"o{i}"), landing_ts_ms=100)

    lq.drain(100)
    assert [o.order_id for o in exch.submitted] == [f"o{i}" for i in range(5)]


# ── Integration with strategy base ──────────────────────────────────────────


def test_strategy_submit_routes_through_latency_queue():
    """When a latency queue is attached, BaseStrategy.submit_order enqueues."""
    from strategy.base import BaseStrategy

    class _TestStrategy(BaseStrategy):
        def on_market_data(self, event): pass
        def on_order_fill(self, fill_event): pass

    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    strategy = _TestStrategy()
    strategy.connect_exchange(exch)
    strategy.connect_latency_queue(lq)
    strategy.update_clock(exch_ts=1000, local_ts=1050)

    result = strategy.submit_order(price=100, side="buy", amount=1.0, order_id="test1")
    assert result is None  # deferred — not submitted yet
    assert lq.pending_count() == 1
    assert len(exch.submitted) == 0

    # Drain at landing time (local_ts + latency_estimate = 1050 + 50 = 1100)
    lq.drain(1100)
    assert len(exch.submitted) == 1
    assert exch.submitted[0].order_id == "test1"


def test_strategy_cancel_uses_inflight_fast_path():
    """Cancel of an inflight order removes it without network delay."""
    from strategy.base import BaseStrategy

    class _TestStrategy(BaseStrategy):
        def on_market_data(self, event): pass
        def on_order_fill(self, fill_event): pass

    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    strategy = _TestStrategy()
    strategy.connect_exchange(exch)
    strategy.connect_latency_queue(lq)
    strategy.update_clock(exch_ts=1000, local_ts=1050)

    strategy.submit_order(price=100, side="buy", amount=1.0, order_id="fast_cancel")
    assert lq.pending_count() == 1

    # Cancel before it lands — should remove from queue
    result = strategy.cancel_order("fast_cancel")
    assert result is True
    assert lq.pending_count() == 0

    lq.drain(9999)
    assert len(exch.submitted) == 0
    assert len(exch.cancelled) == 0


def test_strategy_cancel_enqueues_when_already_landed():
    """Cancel of an order that's already on the book goes through the queue."""
    from strategy.base import BaseStrategy

    class _TestStrategy(BaseStrategy):
        def on_market_data(self, event): pass
        def on_order_fill(self, fill_event): pass

    exch = _MockExchange()
    lq = LatencyQueue(submit_fn=exch.submit_order, cancel_fn=exch.cancel_order)

    strategy = _TestStrategy()
    strategy.connect_exchange(exch)
    strategy.connect_latency_queue(lq)
    strategy.update_clock(exch_ts=1000, local_ts=1050)

    strategy.submit_order(price=100, side="buy", amount=1.0, order_id="landed")
    lq.drain(1100)  # order lands on exchange
    assert len(exch.submitted) == 1

    # Now cancel — order is no longer inflight, so cancel goes through queue
    result = strategy.cancel_order("landed")
    assert result is None  # async cancel
    assert lq.pending_count() == 1

    lq.drain(1200)
    assert exch.cancelled == ["landed"]
