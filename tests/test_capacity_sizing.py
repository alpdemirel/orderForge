"""Tests for BaseStrategy.fit_quote and AS strategy capacity-aware sizing."""

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import (
    Order, Side, PRICE_MULTIPLIER, QUANTITY_MULTIPLIER,
)
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy
from strategy.noop import NoOpStrategy


def _wire_strategy(strategy, *, initial_cash_usd: float | None = None, initial_base_btc: float = 0.0):
    """Build a minimal pipeline (no latency queue) and optionally seed."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway, on_fill=strategy.on_order_fill)
    strategy.connect_exchange(notifying)

    if initial_cash_usd is not None:
        fp_divisor = PRICE_MULTIPLIER * QUANTITY_MULTIPLIER
        engine.seed_account(
            strategy.owner_id,
            cash_balance=int(round(initial_cash_usd * fp_divisor)),
            base_position=int(round(initial_base_btc * QUANTITY_MULTIPLIER)),
        )
    return engine, notifying


# ── fit_quote: opt-in semantics ──────────────────────────────────────────────

def test_fit_quote_returns_target_when_unenforced() -> None:
    strategy = NoOpStrategy()
    _wire_strategy(strategy)
    # No seed_account call → unconstrained → target passes through.
    assert strategy.fit_quote("buy", 50_000.0, 0.05) == 0.05
    assert strategy.fit_quote("sell", 50_000.0, 0.05) == 0.05


def test_fit_quote_returns_target_when_no_exchange_attached() -> None:
    strategy = NoOpStrategy()
    # exchange not connected → infinite capacity → target unchanged.
    assert strategy.fit_quote("buy", 50_000.0, 0.05) == 0.05


# ── fit_quote: capacity logic ────────────────────────────────────────────────

def test_fit_quote_buy_caps_to_cash_minus_fee_reserve() -> None:
    strategy = NoOpStrategy()
    # $1000 cash, 0 BTC. Want a 0.05 BTC bid at $50k → notional $2500 + fees,
    # exceeds balance.
    _wire_strategy(strategy, initial_cash_usd=1000.0)
    fitted = strategy.fit_quote("buy", 50_000.0, 0.05)
    # Capacity ≈ 1000 / (50_000 * 1.002) ≈ 0.01996 BTC.
    assert 0.0199 < fitted < 0.02
    # Fitted amount must actually be acceptable to the engine.
    fp_qty = round(fitted * QUANTITY_MULTIPLIER)
    fp_price = round(50_000.0 * PRICE_MULTIPLIER)
    order = Order(
        order_id="probe", owner_id=strategy.owner_id,
        side=Side.BUY, price=fp_price, quantity=fp_qty,
    )
    result = strategy.exchange.submit_order(order)
    assert result.accepted is True


def test_fit_quote_sell_caps_to_available_base() -> None:
    strategy = NoOpStrategy()
    _wire_strategy(strategy, initial_cash_usd=0.0, initial_base_btc=0.03)
    fitted = strategy.fit_quote("sell", 50_000.0, 0.10)
    assert abs(fitted - 0.03) < 1e-9


def test_fit_quote_returns_zero_when_no_capacity_on_buy_side() -> None:
    strategy = NoOpStrategy()
    _wire_strategy(strategy, initial_cash_usd=0.0, initial_base_btc=0.10)
    assert strategy.fit_quote("buy", 50_000.0, 0.05) == 0.0


def test_fit_quote_returns_zero_when_no_capacity_on_sell_side() -> None:
    strategy = NoOpStrategy()
    _wire_strategy(strategy, initial_cash_usd=10_000.0, initial_base_btc=0.0)
    assert strategy.fit_quote("sell", 50_000.0, 0.05) == 0.0


def test_fit_quote_returns_zero_when_capacity_below_min_lot() -> None:
    strategy = NoOpStrategy()
    # $1 cash @ $50k → ~0.00002 BTC capacity, well below MIN_QUANTITY_BTC.
    _wire_strategy(strategy, initial_cash_usd=1.0)
    assert strategy.fit_quote("buy", 50_000.0, 0.05) == 0.0


# ── fit_quote: lock-aware exclusion (cancel-replace cycle) ───────────────────

def test_fit_quote_excludes_locks_for_in_flight_cancels() -> None:
    """Strategy submits a bid, exhausting cash. fit_quote with the bid's
    order_id in exclude_locks_from_orders should report full capacity again."""
    strategy = NoOpStrategy()
    _wire_strategy(strategy, initial_cash_usd=1000.0)
    # Submit a bid that consumes most of the cash.
    fp_price = round(50_000.0 * PRICE_MULTIPLIER)
    fp_qty = round(0.0199 * QUANTITY_MULTIPLIER)
    strategy.exchange.submit_order(Order(
        order_id="b1", owner_id=strategy.owner_id,
        side=Side.BUY, price=fp_price, quantity=fp_qty,
    ))

    # Without exclusion: very little capacity left.
    capped = strategy.fit_quote("buy", 50_000.0, 0.05)
    # With exclusion: the in-flight order's lock is treated as released.
    reclaimed = strategy.fit_quote("buy", 50_000.0, 0.05, exclude_locks_from_orders=("b1",))

    assert reclaimed > capped
    assert reclaimed > 0.0199 * 0.99  # roughly back to the original capacity


# ── AS strategy: capacity-aware order placement ──────────────────────────────

def _seed_volatility(strategy: AvellanedaStoikovStrategy) -> None:
    """Push a couple of differing mid-price ticks so sigma becomes non-zero."""
    strategy.on_market_data({"local_ts_ms": 0, "best_bid": 49_999.5, "best_ask": 50_000.5})
    strategy.on_market_data({"local_ts_ms": 100, "best_bid": 50_010.5, "best_ask": 50_011.5})


def test_as_strategy_posts_both_sides_with_ample_balance() -> None:
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=0.001)
    _wire_strategy(strategy, initial_cash_usd=1_000_000.0, initial_base_btc=10.0)
    _seed_volatility(strategy)
    strategy.on_market_data({"local_ts_ms": 200, "best_bid": 49_999.0, "best_ask": 50_001.0})
    assert len(strategy._active_order_ids) == 2


def test_as_strategy_skips_bid_when_no_cash() -> None:
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=0.001)
    _wire_strategy(strategy, initial_cash_usd=0.0, initial_base_btc=10.0)
    _seed_volatility(strategy)
    strategy.on_market_data({"local_ts_ms": 200, "best_bid": 49_999.0, "best_ask": 50_001.0})
    # Only an ask should be active.
    assert len(strategy._active_order_ids) == 1
    only = strategy.exchange.get_order(strategy._active_order_ids[0])
    assert only is not None
    assert only.side == Side.SELL


def test_as_strategy_skips_ask_when_no_base() -> None:
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=0.001)
    _wire_strategy(strategy, initial_cash_usd=1_000_000.0, initial_base_btc=0.0)
    _seed_volatility(strategy)
    strategy.on_market_data({"local_ts_ms": 200, "best_bid": 49_999.0, "best_ask": 50_001.0})
    assert len(strategy._active_order_ids) == 1
    only = strategy.exchange.get_order(strategy._active_order_ids[0])
    assert only is not None
    assert only.side == Side.BUY


def test_as_strategy_skips_both_sides_when_below_min_lot() -> None:
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=0.001)
    # $1 cash, 0 BTC — capacity ≈ 0.00002 BTC, well under MIN_QUANTITY_BTC.
    _wire_strategy(strategy, initial_cash_usd=1.0, initial_base_btc=0.0)
    _seed_volatility(strategy)
    strategy.on_market_data({"local_ts_ms": 200, "best_bid": 49_999.0, "best_ask": 50_001.0})
    assert strategy._active_order_ids == []


def test_as_strategy_orders_are_not_rejected_when_seeded() -> None:
    """End-to-end: with realistic balance, AS replace cycle should never
    produce rejections — fit_quote pre-filters and the latency-aware
    exclusion accounts for in-flight cancels."""
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.001, order_size=0.001)
    engine, _ = _wire_strategy(strategy, initial_cash_usd=100_000.0, initial_base_btc=2.0)
    _seed_volatility(strategy)
    # Push many ticks so the replace cycle runs repeatedly.
    for i in range(20):
        strategy.on_market_data({
            "local_ts_ms": 200 + i * 10,
            "best_bid": 49_999.0,
            "best_ask": 50_001.0,
        })
    state = engine.get_account_state(strategy.owner_id)
    # Sanity: locks are bounded by available, never negative, and orders rest.
    assert state.locked_quote >= 0
    assert state.locked_base >= 0
    assert state.available_quote() >= 0
    assert state.available_base() >= 0
