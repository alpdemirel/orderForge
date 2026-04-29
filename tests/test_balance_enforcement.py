"""Tests for the opt-in balance-enforcement layer (locks + initial seeding)."""

from exchange import ExchangeGateway, Order, Side, TimeInForce
from exchange.engine import ExchangeEngine
from exchange.models import OrderStatus

# Fixed-point convention: cash and notional are price_ticks * satoshis,
# base is satoshis. Tests use modest integers so arithmetic stays readable.


def _make_gateway_with_seed(cash: int = 0, base: int = 0, owner: str = "trader") -> ExchangeGateway:
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    engine.seed_account(owner, cash_balance=cash, base_position=base)
    return gateway


# ── Opt-in semantics ─────────────────────────────────────────────────────────

def test_unseeded_owner_runs_unconstrained() -> None:
    """Owners without seed_account() must behave exactly as before — no
    balance check, no lock tracking. Guarantees backward compatibility."""
    gateway = ExchangeGateway()
    order = Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(order)
    assert result.accepted is True
    state = gateway.get_account_state("trader")
    assert state.locked_quote == 0
    assert state.locked_base == 0


# ── Rejection on insufficient balance ────────────────────────────────────────

def test_buy_rejected_when_cash_below_notional_plus_fee() -> None:
    # Notional = 1000 * 100_000_000 = 1e11; taker fee 20 bps = 2e8.
    # Required = 100_200_000_000. Seed 1 short of that.
    gateway = _make_gateway_with_seed(cash=100_199_999_999)
    order = Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(order)
    assert result.accepted is False
    assert result.reason == "insufficient_quote_balance"
    assert order.status == OrderStatus.REJECTED


def test_buy_accepted_at_exact_required_balance() -> None:
    gateway = _make_gateway_with_seed(cash=100_200_000_000)
    order = Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(order)
    assert result.accepted is True
    state = gateway.get_account_state("trader")
    assert state.locked_quote == 100_200_000_000
    assert state.available_quote() == 0


def test_sell_rejected_when_base_below_quantity() -> None:
    gateway = _make_gateway_with_seed(base=99_999_999)
    order = Order(order_id="s1", owner_id="trader", side=Side.SELL, price=1000, quantity=100_000_000)
    result = gateway.submit_order(order)
    assert result.accepted is False
    assert result.reason == "insufficient_base_balance"


def test_sell_accepted_when_base_sufficient() -> None:
    gateway = _make_gateway_with_seed(base=100_000_000)
    order = Order(order_id="s1", owner_id="trader", side=Side.SELL, price=1000, quantity=100_000_000)
    result = gateway.submit_order(order)
    assert result.accepted is True
    state = gateway.get_account_state("trader")
    assert state.locked_base == 100_000_000
    assert state.available_base() == 0


# ── Lock release on cancel ───────────────────────────────────────────────────

def test_cancel_releases_full_buy_lock() -> None:
    gateway = _make_gateway_with_seed(cash=100_200_000_000)
    gateway.submit_order(Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000))
    state = gateway.get_account_state("trader")
    assert state.locked_quote == 100_200_000_000

    assert gateway.cancel_order("b1") is True
    assert state.locked_quote == 0
    assert state.available_quote() == 100_200_000_000


def test_cancel_releases_sell_lock_back_to_available_base() -> None:
    gateway = _make_gateway_with_seed(base=100_000_000)
    gateway.submit_order(Order(order_id="s1", owner_id="trader", side=Side.SELL, price=1000, quantity=100_000_000))
    state = gateway.get_account_state("trader")
    assert state.available_base() == 0

    gateway.cancel_order("s1")
    assert state.locked_base == 0
    assert state.available_base() == 100_000_000


# ── Lock release on fill ─────────────────────────────────────────────────────

def test_full_fill_releases_taker_lock() -> None:
    """Crossing taker locks at submit, then fully fills — lock returns to 0
    and the actual cost is reflected in cash_balance via apply_trade."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    engine.seed_account("taker", cash_balance=100_200_000_000)
    # Maker is unconstrained (legacy unseeded owner).
    gateway.submit_order(Order(order_id="m1", owner_id="maker", side=Side.SELL, price=1000, quantity=100_000_000))
    result = gateway.submit_order(Order(order_id="t1", owner_id="taker", side=Side.BUY, price=1000, quantity=100_000_000))

    assert result.accepted is True
    taker = gateway.get_account_state("taker")
    assert taker.locked_quote == 0  # released after full fill
    # Notional 1e11 + taker fee 2e8 = 1.002e11 deducted; matches lock.
    assert taker.cash_balance == 0


def test_partial_fill_releases_proportional_lock_for_resting_maker() -> None:
    """A seeded maker rests an ask, market hits half — locked_base should
    drop by exactly the filled amount."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    engine.seed_account("mm", base_position=200_000_000)
    gateway.submit_order(Order(order_id="ask1", owner_id="mm", side=Side.SELL, price=1000, quantity=200_000_000))

    # An unseeded taker buys half.
    gateway.submit_order(Order(order_id="t1", owner_id="aggressor", side=Side.BUY, price=1000, quantity=100_000_000))

    mm_state = gateway.get_account_state("mm")
    # Sold 100m of 200m. apply_trade dropped base_position to 100m; lock
    # release dropped locked_base to 100m (the still-resting half).
    assert mm_state.base_position == 100_000_000
    assert mm_state.locked_base == 100_000_000
    assert mm_state.available_base() == 0  # remaining inventory is committed


# ── Bid + ask both lock independently ────────────────────────────────────────

def test_simultaneous_bid_and_ask_lock_separate_buckets() -> None:
    """The AS-strategy case: a resting bid locks quote; a resting ask locks
    base. They must not cannibalize each other."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    engine.seed_account("mm", cash_balance=100_200_000_000, base_position=100_000_000)

    bid = gateway.submit_order(Order(order_id="b1", owner_id="mm", side=Side.BUY, price=1000, quantity=100_000_000))
    ask = gateway.submit_order(Order(order_id="a1", owner_id="mm", side=Side.SELL, price=1010, quantity=100_000_000))

    assert bid.accepted and ask.accepted
    state = gateway.get_account_state("mm")
    assert state.locked_quote == 100_200_000_000
    assert state.locked_base == 100_000_000
    assert state.available_quote() == 0
    assert state.available_base() == 0


def test_second_buy_rejected_when_first_lock_consumes_balance() -> None:
    """Two resting bids must stack their locks — second one should be
    rejected if total exceeds available cash."""
    gateway = _make_gateway_with_seed(cash=100_200_000_000)
    first = gateway.submit_order(Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000))
    second = gateway.submit_order(Order(order_id="b2", owner_id="trader", side=Side.BUY, price=1000, quantity=1))

    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "insufficient_quote_balance"


# ── IOC unfilled remainder releases lock ─────────────────────────────────────

def test_ioc_unfilled_releases_lock() -> None:
    """IOC that finds no liquidity should refund the lock entirely."""
    gateway = _make_gateway_with_seed(cash=100_200_000_000)
    result = gateway.submit_order(
        Order(order_id="ioc1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000, tif=TimeInForce.IOC)
    )
    assert result.accepted is True
    assert result.reason == "ioc_unfilled_canceled"
    state = gateway.get_account_state("trader")
    assert state.locked_quote == 0
    assert state.available_quote() == 100_200_000_000


def test_ioc_partial_fill_releases_lock_for_unfilled_remainder() -> None:
    """IOC fills 50% then cancels rest — total lock should be 0 after."""
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    engine.seed_account("taker", cash_balance=100_200_000_000)
    gateway.submit_order(Order(order_id="m1", owner_id="maker", side=Side.SELL, price=1000, quantity=50_000_000))

    result = gateway.submit_order(
        Order(order_id="ioc1", owner_id="taker", side=Side.BUY, price=1000, quantity=100_000_000, tif=TimeInForce.IOC)
    )
    assert result.accepted is True
    state = gateway.get_account_state("taker")
    assert state.locked_quote == 0  # filled portion settled, unfilled portion refunded
    # Spent: notional 5e10 + taker fee 1e8 = 5.01e10; started with 1.002e11.
    assert state.cash_balance == 100_200_000_000 - 50_100_000_000


# ── Rejection when no lock is taken (REJECT path leaves account untouched) ───

def test_rejected_order_leaves_locks_unchanged() -> None:
    gateway = _make_gateway_with_seed(cash=100_000)  # tiny balance
    state_before = gateway.get_account_state("trader")
    locked_before = state_before.locked_quote

    result = gateway.submit_order(
        Order(order_id="b1", owner_id="trader", side=Side.BUY, price=1000, quantity=100_000_000)
    )
    assert result.accepted is False
    assert state_before.locked_quote == locked_before  # unchanged
