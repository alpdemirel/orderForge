"""Tests for queue position modeling in match_incoming_against_strategy.

Verifies that market orders sitting at the same price level as a strategy
order consume incoming liquidity before the strategy order gets filled.
"""

from exchange.engine import ExchangeEngine
from exchange.models import Fill, MARKET_OWNER_ID, Order, Side, TimeInForce


def _market_order(order_id: str, side: Side, price: int, quantity: int) -> Order:
    return Order(
        order_id=order_id,
        side=side,
        price=price,
        quantity=quantity,
        owner_id=MARKET_OWNER_ID,
        tif=TimeInForce.GTC,
    )


def _strategy_order(order_id: str, side: Side, price: int, quantity: int) -> Order:
    return Order(
        order_id=order_id,
        side=side,
        price=price,
        quantity=quantity,
        owner_id="strat",
        tif=TimeInForce.GTC,
    )


# ── Queue depth calculation ──────────────────────────────────────────────────


def test_market_depth_ahead_empty():
    engine = ExchangeEngine()
    assert engine.book.market_depth_ahead(Side.SELL, 1000, MARKET_OWNER_ID) == 0


def test_market_depth_ahead_counts_market_orders():
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.SELL, 1000, 500))
    engine.book.add(_market_order("m2", Side.SELL, 1000, 300))
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))

    depth = engine.book.market_depth_ahead(Side.SELL, 1000, MARKET_OWNER_ID)
    assert depth == 800  # only market orders counted


def test_market_depth_ahead_ignores_other_prices():
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.SELL, 1000, 500))
    engine.book.add(_market_order("m2", Side.SELL, 1010, 300))

    depth = engine.book.market_depth_ahead(Side.SELL, 1000, MARKET_OWNER_ID)
    assert depth == 500  # only at price 1000


# ── Strategy fill with queue ahead ──────────────────────────────────────────


def test_no_fill_when_market_queue_absorbs_all():
    """Incoming buy of 500 at price 1000, but 800 market asks ahead of
    strategy ask at 1000 → strategy gets nothing."""
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.SELL, 1000, 500))
    engine.book.add(_market_order("m2", Side.SELL, 1000, 300))
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))

    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1000, quantity=500, bitfinex_id=999,
    )
    assert len(fills) == 0
    # Strategy order should still be on the book
    assert engine.get_order("s1") is not None
    assert engine.get_order("s1").remaining == 200


def test_partial_fill_after_queue():
    """Incoming buy of 1000 at price 1000, with 800 market asks ahead.
    Strategy gets 200 (1000 - 800)."""
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.SELL, 1000, 500))
    engine.book.add(_market_order("m2", Side.SELL, 1000, 300))
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))

    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1000, quantity=1000, bitfinex_id=999,
    )
    assert len(fills) == 1
    assert fills[0].maker_order_id == "s1"
    assert fills[0].quantity == 200


def test_full_fill_when_no_market_queue():
    """No market orders ahead — strategy order fills immediately."""
    engine = ExchangeEngine()
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))

    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1000, quantity=200, bitfinex_id=999,
    )
    assert len(fills) == 1
    assert fills[0].quantity == 200
    assert engine.get_order("s1") is None  # fully filled


def test_fill_across_price_levels_with_queue():
    """Strategy has orders at two price levels. Market queue at each level
    determines effective fill."""
    engine = ExchangeEngine()
    # Level 1000: 300 market + 100 strategy
    engine.book.add(_market_order("m1", Side.SELL, 1000, 300))
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 100))
    # Level 1010: 0 market + 50 strategy
    engine.book.add(_strategy_order("s2", Side.SELL, 1010, 50))

    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1010, quantity=500, bitfinex_id=999,
    )
    # At 1000: 500 incoming - 300 market ahead = 200 effective, fills 100 (all of s1)
    # remaining = 500 - 100 = 400
    # At 1010: 400 incoming - 0 market ahead = 400 effective, fills 50 (all of s2)
    assert len(fills) == 2
    assert fills[0].maker_order_id == "s1"
    assert fills[0].quantity == 100
    assert fills[1].maker_order_id == "s2"
    assert fills[1].quantity == 50


def test_bid_side_queue_position():
    """Queue position works for bid-side strategy orders too."""
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.BUY, 1000, 400))
    engine.book.add(_strategy_order("s1", Side.BUY, 1000, 100))

    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.SELL, price=1000, quantity=300, bitfinex_id=999,
    )
    # 300 incoming - 400 market ahead = -100 effective → no fill
    assert len(fills) == 0
    assert engine.get_order("s1") is not None


def test_accounting_applied_after_queue_fill():
    """Verify fee accounting is correct when queue reduces fill quantity."""
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.SELL, 1000, 300))
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))

    fills, _ = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1000, quantity=400, bitfinex_id=999,
    )
    assert len(fills) == 1
    assert fills[0].quantity == 100  # 400 - 300 market ahead

    # Check accounting
    account = engine.get_account_state("strat")
    assert account.base_position == -100  # sold 100
    assert account.fees_paid > 0
    assert engine.get_fee_pool() > 0


# ── Sequence-aware queue position (FIFO arrival order) ─────────────────────


def test_market_orders_after_strategy_not_counted_ahead():
    """Market orders that arrived AFTER the strategy order should not
    count as queue depth ahead — only orders with a lower book_seq
    are ahead in the real FIFO queue."""
    engine = ExchangeEngine()
    # Strategy order placed first
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))
    # Market orders arrive later (higher book_seq)
    engine.book.add(_market_order("m1", Side.SELL, 1000, 500))
    engine.book.add(_market_order("m2", Side.SELL, 1000, 300))

    # Incoming buy of 200 — with old logic this would see 800 market
    # ahead and produce no fill.  With seq-aware logic, 0 market orders
    # are ahead (both arrived after the strategy order), so full fill.
    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1000, quantity=200, bitfinex_id=999,
    )
    assert len(fills) == 1
    assert fills[0].maker_order_id == "s1"
    assert fills[0].quantity == 200


def test_mixed_arrival_order_partial_queue():
    """Some market orders before strategy, some after.  Only the ones
    that arrived before should count as ahead."""
    engine = ExchangeEngine()
    # m1 arrives first (seq 0)
    engine.book.add(_market_order("m1", Side.SELL, 1000, 300))
    # Strategy arrives second (seq 1)
    engine.book.add(_strategy_order("s1", Side.SELL, 1000, 200))
    # m2 arrives third (seq 2) — should NOT count as ahead
    engine.book.add(_market_order("m2", Side.SELL, 1000, 500))

    # Incoming buy of 400 at 1000.
    # Only m1 (300) is ahead.  effective = 400 - 300 = 100 → partial fill.
    fills, remaining = engine.match_incoming_against_strategy(
        incoming_side=Side.BUY, price=1000, quantity=400, bitfinex_id=999,
    )
    assert len(fills) == 1
    assert fills[0].maker_order_id == "s1"
    assert fills[0].quantity == 100


def test_depth_ahead_with_before_seq():
    """Direct test of market_depth_ahead with before_seq filtering."""
    engine = ExchangeEngine()
    engine.book.add(_market_order("m1", Side.SELL, 1000, 100))  # seq 0
    engine.book.add(_market_order("m2", Side.SELL, 1000, 200))  # seq 1
    s1 = _strategy_order("s1", Side.SELL, 1000, 50)
    engine.book.add(s1)                                          # seq 2
    engine.book.add(_market_order("m3", Side.SELL, 1000, 400))  # seq 3

    # Without seq filter: all market orders = 100 + 200 + 400 = 700
    total = engine.book.market_depth_ahead(Side.SELL, 1000, MARKET_OWNER_ID)
    assert total == 700

    # With seq filter: only m1 + m2 (seq < 2) = 300
    ahead = engine.book.market_depth_ahead(
        Side.SELL, 1000, MARKET_OWNER_ID, before_seq=s1.book_seq,
    )
    assert ahead == 300
