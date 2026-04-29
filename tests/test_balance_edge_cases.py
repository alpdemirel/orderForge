"""Edge cases for the balance-enforcement layer: re-seed semantics, market
owner immunity, latency-queue replace cycles, and audit logging."""

import logging

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import (
    MARKET_OWNER_ID, Order, Side, PRICE_MULTIPLIER, QUANTITY_MULTIPLIER,
)
from sim.latency_queue import LatencyQueue
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy


# ── Re-seeding ───────────────────────────────────────────────────────────────

def test_seed_account_overwrites_previous_state() -> None:
    """Calling seed_account twice replaces the prior balance, not adds to
    it. Catches accidental cumulative seeding."""
    engine = ExchangeEngine()
    engine.seed_account("trader", cash_balance=1_000, base_position=10)
    engine.seed_account("trader", cash_balance=500, base_position=2)
    state = engine.get_account_state("trader")
    assert state.cash_balance == 500
    assert state.base_position == 2
    # Locks should also be reset to zero on re-seed.
    assert state.locked_quote == 0
    assert state.locked_base == 0


def test_re_seed_clears_active_locks() -> None:
    """If an order is resting and we re-seed, the new state starts clean."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    engine.seed_account("trader", cash_balance=100_200_000_000)
    gateway.submit_order(Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000))
    state = engine.get_account_state("trader")
    assert state.locked_quote > 0

    engine.seed_account("trader", cash_balance=50_000_000_000)
    state = engine.get_account_state("trader")
    assert state.cash_balance == 50_000_000_000
    assert state.locked_quote == 0  # reset


# ── MARKET owner immunity ────────────────────────────────────────────────────

def test_market_owner_skips_balance_check_even_when_seeded() -> None:
    """L3 market liquidity must always be admitted regardless of any state
    we might have stamped on the MARKET_OWNER_ID account."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    # Even if someone (mistakenly) seeds the market owner with zero balance,
    # market orders must still be accepted.
    engine.seed_account(MARKET_OWNER_ID, cash_balance=0, base_position=0)

    order = Order(order_id="mkt1", owner_id=MARKET_OWNER_ID, side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(order)
    assert result.accepted is True


def test_market_owner_orders_have_no_lock_tracking() -> None:
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    order = Order(order_id="mkt1", owner_id=MARKET_OWNER_ID, side=Side.BUY, price=1000, quantity=100_000_000)
    gateway.submit_order(order)
    assert order.locked_quote == 0
    assert order.locked_base == 0


# ── Latency-queue replace cycle ──────────────────────────────────────────────

def test_replace_cycle_through_latency_queue_does_not_overdraft() -> None:
    """The AS replace pattern: cancel old quotes + submit new ones, all in
    the same tick.  Both are queued; cancels land before submits because
    they were enqueued earlier with the same landing_ts.  The new submits
    must be accepted because the old locks will have been released by then."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway)

    # Just enough cash for ONE bid clip — second bid alone would be rejected.
    cash = 100_200_000_000
    engine.seed_account("trader", cash_balance=cash)

    lq = LatencyQueue(submit_fn=notifying.submit_order, cancel_fn=notifying.cancel_order)

    # Submit B1 directly to lock the cash up.
    gateway.submit_order(Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000))
    state = engine.get_account_state("trader")
    assert state.locked_quote == cash

    # Now enqueue: cancel(b1) followed by submit(b2). Both at ts=10.
    lq.enqueue_cancel("b1", 10)
    b2 = Order(order_id="b2", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000)
    lq.enqueue_submit(b2, 10)

    lq.drain(10)

    # B1 was canceled, lock released, then B2 was submitted and re-locked.
    assert engine.get_order("b1") is None
    assert engine.get_order("b2") is not None
    assert state.locked_quote == cash  # B2 holds the lock now
    assert b2.status != b2.status.REJECTED  # type: ignore[attr-defined]


# ── AS audit logging ─────────────────────────────────────────────────────────

def _wire_as_with_seed(initial_cash_usd: float, initial_base_btc: float = 0.0):
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=0.01)
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway, on_fill=strategy.on_order_fill)
    strategy.connect_exchange(notifying)

    fp_divisor = PRICE_MULTIPLIER * QUANTITY_MULTIPLIER
    engine.seed_account(
        strategy.owner_id,
        cash_balance=int(round(initial_cash_usd * fp_divisor)),
        base_position=int(round(initial_base_btc * QUANTITY_MULTIPLIER)),
    )
    return strategy, engine


def test_audit_logs_warning_when_clip_exceeds_half_of_cash(caplog) -> None:
    strategy, _ = _wire_as_with_seed(initial_cash_usd=500.0)  # clip 0.01 * 50k = $500 notional > $250
    with caplog.at_level(logging.WARNING, logger="strategy.avellaneda_stoikov"):
        strategy.on_market_data({"local_ts_ms": 0, "best_bid": 49_999.5, "best_ask": 50_000.5})
    assert any("Bid clip" in rec.message and "50%" in rec.message for rec in caplog.records)


def test_audit_logs_error_when_account_below_min_lot_capacity(caplog) -> None:
    strategy, _ = _wire_as_with_seed(initial_cash_usd=1.0)
    with caplog.at_level(logging.ERROR, logger="strategy.avellaneda_stoikov"):
        strategy.on_market_data({"local_ts_ms": 0, "best_bid": 49_999.5, "best_ask": 50_000.5})
    assert any("Account too small" in rec.message for rec in caplog.records)


def test_audit_runs_only_once(caplog) -> None:
    strategy, _ = _wire_as_with_seed(initial_cash_usd=500.0)
    with caplog.at_level(logging.WARNING, logger="strategy.avellaneda_stoikov"):
        strategy.on_market_data({"local_ts_ms": 0, "best_bid": 49_999.5, "best_ask": 50_000.5})
        strategy.on_market_data({"local_ts_ms": 100, "best_bid": 49_999.5, "best_ask": 50_000.5})
        strategy.on_market_data({"local_ts_ms": 200, "best_bid": 49_999.5, "best_ask": 50_000.5})
    bid_clip_warnings = [r for r in caplog.records if "Bid clip" in r.message]
    assert len(bid_clip_warnings) == 1


def test_audit_silent_when_unenforced(caplog) -> None:
    """Unenforced strategies must not log any audit warnings/errors."""
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=100.0)  # absurdly large clip
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway, on_fill=strategy.on_order_fill)
    strategy.connect_exchange(notifying)
    # No seed_account — strategy is unconstrained, audit should be silent.

    with caplog.at_level(logging.WARNING, logger="strategy.avellaneda_stoikov"):
        strategy.on_market_data({"local_ts_ms": 0, "best_bid": 49_999.5, "best_ask": 50_000.5})
    assert caplog.records == []


# ── Inventory sync from seeded base ──────────────────────────────────────────

def test_seeded_base_reflected_in_inventory_position_on_first_tick() -> None:
    """If the user starts with 1 BTC long, the AS reservation-price math
    must see q=1.0 (which skews the quote toward selling) on the very first
    tick — not q=0 inherited from the constructor."""
    strategy, _ = _wire_as_with_seed(initial_cash_usd=100_000.0, initial_base_btc=1.0)
    assert strategy.inventory_position == 0.0  # before any market data

    strategy.on_market_data({"local_ts_ms": 0, "best_bid": 49_999.5, "best_ask": 50_000.5})
    assert abs(strategy.inventory_position - 1.0) < 1e-9
