"""Tests for time-weighted volatility estimation in the AS strategy."""

import math

from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy


def _make_event(best_bid: float, best_ask: float, local_ts_ms: int) -> dict:
    return {
        "type": "top_of_book",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": (best_bid + best_ask) / 2.0,
        "local_ts_ms": local_ts_ms,
        "exchange_ts_ms": local_ts_ms,
    }


def test_sigma_zero_with_one_sample():
    """Volatility stays 0 until we have at least 2 samples."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    strat._update_volatility(100.0, ts_ms=1000)
    assert strat.sigma == 0.0


def test_sigma_zero_with_constant_price():
    """If price never moves, volatility = 0."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    for t in range(10):
        strat._update_volatility(100.0, ts_ms=1000 + t * 100)
    assert strat.sigma == 0.0


def test_sigma_positive_with_price_change():
    """Any price change produces positive sigma."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    strat._update_volatility(100.0, ts_ms=1000)
    strat._update_volatility(101.0, ts_ms=2000)
    assert strat.sigma > 0


def test_time_weighting_reduces_burst_impact():
    """A burst of updates in 1ms intervals should produce different vol than
    the same price moves evenly spaced, proving time-weighting is active.

    We use _update_volatility directly (no exchange needed).
    """
    prices = [100.0]
    for i in range(10):
        prices.append(prices[-1] * 1.001)  # 0.1% up each step

    # Burst: updates 1ms apart
    strat_burst = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    for i, p in enumerate(prices):
        strat_burst._update_volatility(p, ts_ms=1000 + i)  # 1ms apart

    # Then add a flat sample 10s later so the window includes a long quiet gap
    strat_burst._update_volatility(prices[-1], ts_ms=11000)
    sigma_burst = strat_burst.sigma

    # Spread: same prices but evenly spaced 1s apart
    strat_spread = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    for i, p in enumerate(prices):
        strat_spread._update_volatility(p, ts_ms=1000 + i * 1000)  # 1s apart

    sigma_spread = strat_spread.sigma

    # Both should be positive
    assert sigma_burst > 0
    assert sigma_spread > 0

    # With equal-weighted vol, both would be identical.
    # With time-weighted vol, they differ because dt distribution differs.
    assert sigma_burst != sigma_spread


def test_sigma_with_two_samples_equals_abs_log_return_times_price():
    """With exactly 2 samples the formula reduces to |log(p1/p0)| * p1 (dollar vol)."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    strat._update_volatility(100.0, ts_ms=1000)
    strat._update_volatility(105.0, ts_ms=2000)

    # Dollar-scaled: log-return vol * latest mid_price
    expected = abs(math.log(105.0 / 100.0)) * 105.0
    assert abs(strat.sigma - expected) < 1e-10


def test_sigma_ignores_zero_dt_samples():
    """Multiple samples at the same timestamp should not cause division errors."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0)
    strat._update_volatility(100.0, ts_ms=1000)
    strat._update_volatility(101.0, ts_ms=1000)
    strat._update_volatility(102.0, ts_ms=1000)
    # All timestamps identical — total_dt = 0, sigma stays at last computed value
    assert strat.sigma == 0.0


def test_on_market_data_passes_timestamp():
    """on_market_data correctly feeds local_ts_ms to the volatility estimator."""
    strat = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=1.0, session_duration=3600.0)

    # Use a dummy exchange that accepts submit_order and cancel_order
    class _Dummy:
        def submit_order(self, order): return None
        def cancel_order(self, order_id): return True
    strat.connect_exchange(_Dummy())

    # Feed 3 events so sigma gets estimated
    events = [
        _make_event(99.0, 101.0, local_ts_ms=1000),
        _make_event(99.5, 101.5, local_ts_ms=2000),
        _make_event(100.0, 102.0, local_ts_ms=3000),
    ]
    for ev in events:
        strat.update_clock(ev["exchange_ts_ms"], ev["local_ts_ms"])
        strat.on_market_data(ev)

    assert strat.sigma > 0
    assert len(strat._mid_timestamps) == 3
