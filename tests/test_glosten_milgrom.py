"""Unit tests for the Glosten-Milgrom strategy."""

import pytest

from strategy.glosten_milgrom import GlostenMilgromStrategy
from exchange.models import Fill, Side, QUANTITY_MULTIPLIER


def _make_fill(taker_side: Side, *, maker_owner: str = "other",
               taker_owner: str = "other", qty: int = 1_000_000) -> Fill:
    return Fill(
        maker_order_id="m1",
        taker_order_id="t1",
        maker_owner_id=maker_owner,
        taker_owner_id=taker_owner,
        maker_side=Side.SELL if taker_side == Side.BUY else Side.BUY,
        taker_side=taker_side,
        price=680_000_000,
        quantity=qty,
    )


def test_construction_validates_parameters():
    with pytest.raises(ValueError):
        GlostenMilgromStrategy(informed_fraction=0.0)
    with pytest.raises(ValueError):
        GlostenMilgromStrategy(informed_fraction=1.0)
    with pytest.raises(ValueError):
        GlostenMilgromStrategy(value_half_range_bps=0)
    with pytest.raises(ValueError):
        GlostenMilgromStrategy(belief_decay=1.0)
    with pytest.raises(ValueError):
        GlostenMilgromStrategy(order_size=0)
    with pytest.raises(ValueError):
        GlostenMilgromStrategy(session_duration=-1)


def test_default_belief_is_neutral():
    strat = GlostenMilgromStrategy()
    assert strat.belief == pytest.approx(0.5)


def test_bayes_update_buy_raises_belief():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    strat.on_order_fill(_make_fill(Side.BUY))
    assert strat.belief > 0.5


def test_bayes_update_sell_lowers_belief():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    strat.on_order_fill(_make_fill(Side.SELL))
    assert strat.belief < 0.5


def test_symmetric_buy_sell_restores_neutral_belief():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    strat.on_order_fill(_make_fill(Side.BUY))
    strat.on_order_fill(_make_fill(Side.SELL))
    # One buy then one sell should cancel under symmetric Bayes update.
    assert strat.belief == pytest.approx(0.5, abs=1e-9)


def test_belief_stays_in_bounds_under_repeated_same_side():
    strat = GlostenMilgromStrategy(informed_fraction=0.5, belief_decay=0.0)
    for _ in range(200):
        strat.on_order_fill(_make_fill(Side.BUY))
    assert strat.belief < 1.0
    assert strat.belief > 0.0


def test_compute_quotes_symmetric_at_neutral_belief():
    strat = GlostenMilgromStrategy(
        informed_fraction=0.3,
        value_half_range_bps=20.0,
        belief_decay=0.0,
    )
    mid = 68_000.0
    bid, ask = strat.compute_quotes(mid)
    assert bid < mid < ask
    # Neutral belief → symmetric around mid.
    assert abs((mid - bid) - (ask - mid)) < 1e-6


def test_compute_quotes_skews_up_when_belief_high():
    strat = GlostenMilgromStrategy(
        informed_fraction=0.3,
        value_half_range_bps=20.0,
        belief_decay=0.0,
    )
    strat.belief = 0.8
    bid, ask = strat.compute_quotes(68_000.0)
    assert (bid + ask) / 2.0 > 68_000.0


def test_compute_quotes_skews_down_when_belief_low():
    strat = GlostenMilgromStrategy(
        informed_fraction=0.3,
        value_half_range_bps=20.0,
        belief_decay=0.0,
    )
    strat.belief = 0.2
    bid, ask = strat.compute_quotes(68_000.0)
    assert (bid + ask) / 2.0 < 68_000.0


def test_spread_widens_with_informed_fraction():
    mid = 68_000.0
    low = GlostenMilgromStrategy(
        informed_fraction=0.05, value_half_range_bps=20.0, belief_decay=0.0,
    )
    high = GlostenMilgromStrategy(
        informed_fraction=0.8, value_half_range_bps=20.0, belief_decay=0.0,
    )
    bid_l, ask_l = low.compute_quotes(mid)
    bid_h, ask_h = high.compute_quotes(mid)
    # More informed traders → wider adverse-selection spread.
    assert (ask_h - bid_h) > (ask_l - bid_l)


def test_inventory_update_on_our_buy_fill():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    # Taker sold into our bid → we bought.
    fill = _make_fill(Side.SELL, maker_owner="default")
    strat.on_order_fill(fill)
    assert strat.inventory_position == pytest.approx(fill.quantity / QUANTITY_MULTIPLIER)


def test_inventory_update_on_our_sell_fill():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    fill = _make_fill(Side.BUY, maker_owner="default")
    strat.on_order_fill(fill)
    assert strat.inventory_position == pytest.approx(-fill.quantity / QUANTITY_MULTIPLIER)


def test_decay_pulls_belief_toward_neutral():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.5)
    strat.belief = 0.9
    strat._decay_belief()
    assert 0.5 < strat.belief < 0.9


class _FakeExchange:
    def __init__(self):
        self.submitted: list = []
        self.canceled: list = []

    def submit_order(self, order):
        self.submitted.append(order)
        return {"accepted": True, "fills": []}

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        return True


def test_on_market_data_submits_two_quotes():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    strat.connect_exchange(_FakeExchange())
    strat.update_clock(exch_ts=1_000, local_ts=1_050)
    strat.on_market_data({
        "local_ts_ms": 1_050,
        "exch_ts_ms": 1_000,
        "best_bid": 67_999.5,
        "best_ask": 68_000.5,
    })
    assert len(strat.exchange.submitted) == 2
    assert {o.side for o in strat.exchange.submitted} == {Side.BUY, Side.SELL}


def test_cancel_replace_on_second_tick():
    strat = GlostenMilgromStrategy(informed_fraction=0.3, belief_decay=0.0)
    strat.connect_exchange(_FakeExchange())
    strat.update_clock(exch_ts=1_000, local_ts=1_050)
    tick = {
        "local_ts_ms": 1_050,
        "exch_ts_ms": 1_000,
        "best_bid": 67_999.5,
        "best_ask": 68_000.5,
    }
    strat.on_market_data(tick)
    strat.on_market_data(tick)
    # Second tick cancels the two quotes from the first tick.
    assert len(strat.exchange.canceled) == 2
    # Four total submits (two per tick).
    assert len(strat.exchange.submitted) == 4
