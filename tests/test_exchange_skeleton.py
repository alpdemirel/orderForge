from exchange import ExchangeGateway, Order, Side, TimeInForce
from exchange.models import OrderStatus


# ── Existing tests (unchanged) ────────────────────────────────────────────────

def test_post_only_rejected_when_crossing() -> None:
    gateway = ExchangeGateway()
    sell = Order(order_id="s1", side=Side.SELL, price=1000, quantity=100_000_000)
    gateway.submit_order(sell)

    buy_post_only = Order(
        order_id="b1",
        side=Side.BUY,
        price=1000,
        quantity=100_000_000,
        tif=TimeInForce.POST_ONLY,
    )
    result = gateway.submit_order(buy_post_only)

    assert result.accepted is False
    assert result.reason == "post_only_would_cross"


def test_ioc_unfilled_is_canceled() -> None:
    gateway = ExchangeGateway()
    ioc = Order(order_id="ioc1", side=Side.BUY, price=1000, quantity=100_000_000, tif=TimeInForce.IOC)
    result = gateway.submit_order(ioc)

    assert result.accepted is True
    assert result.reason == "ioc_unfilled_canceled"


def test_gtc_order_rests_when_no_match() -> None:
    gateway = ExchangeGateway()
    buy = Order(order_id="b_rest", side=Side.BUY, price=990, quantity=200_000_000)
    result = gateway.submit_order(buy)

    assert result.accepted is True
    assert result.fills == []
    resting = gateway.get_order("b_rest")
    assert resting is not None
    assert resting.remaining == 200_000_000


def test_crossing_order_fills_and_updates_account_hooks() -> None:
    gateway = ExchangeGateway()
    maker = Order(order_id="s_maker", owner_id="maker_1", side=Side.SELL, price=1000, quantity=150_000_000)
    gateway.submit_order(maker)

    taker = Order(
        order_id="b_taker",
        owner_id="taker_1",
        side=Side.BUY,
        price=1010,
        quantity=100_000_000,
        tif=TimeInForce.GTC,
    )
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1
    assert result.fills[0].maker_order_id == "s_maker"
    assert result.fills[0].taker_order_id == "b_taker"
    assert result.taker_fee > 0

    taker_account = gateway.get_account_state("taker_1")
    maker_account = gateway.get_account_state("maker_1")
    assert taker_account.base_position == 100_000_000
    assert maker_account.base_position == -100_000_000
    assert gateway.get_fee_pool() > 0

    maker_order = gateway.get_order("s_maker")
    assert maker_order is not None
    assert maker_order.remaining == 50_000_000


def test_cancel_order_returns_false_for_unknown_id() -> None:
    gateway = ExchangeGateway()
    assert gateway.cancel_order("missing-order") is False


def test_matching_is_deterministic_for_same_input_sequence() -> None:
    def run_sequence() -> tuple[list[tuple[str, str, int, int]], int]:
        gateway = ExchangeGateway()
        gateway.submit_order(Order(order_id="s1", owner_id="m1", side=Side.SELL, price=1010, quantity=100_000_000))
        gateway.submit_order(Order(order_id="s2", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))
        result = gateway.submit_order(
            Order(order_id="b1", owner_id="t1", side=Side.BUY, price=1010, quantity=150_000_000)
        )
        fills = [
            (fill.maker_order_id, fill.taker_order_id, fill.price, fill.quantity) for fill in result.fills
        ]
        return fills, result.taker_fee

    first = run_sequence()
    second = run_sequence()
    assert first == second


# ── Partial fill ──────────────────────────────────────────────────────────────

def test_gtc_taker_partially_fills_remainder_rests_on_book() -> None:
    """
    Maker sells 200_000_000 at 1000.
    Taker buys 100_000_000 at 1000.

    notional = 1000 * 100_000_000 = 100_000_000_000
    taker_fee = (100_000_000_000 * 20) // 10_000 = 200_000_000
    maker_fee = (100_000_000_000 * 10) // 10_000 = 100_000_000
    taker cash_balance = -(100_000_000_000 + 200_000_000) = -100_200_000_000
    """
    gateway = ExchangeGateway()
    maker = Order(order_id="s1", owner_id="maker_1", side=Side.SELL, price=1000, quantity=200_000_000)
    gateway.submit_order(maker)

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1
    assert result.fills[0].price == 1000
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 200_000_000

    assert gateway.get_order("b1") is None

    resting_maker = gateway.get_order("s1")
    assert resting_maker is not None
    assert resting_maker.remaining == 100_000_000
    assert resting_maker.status == OrderStatus.PARTIALLY_FILLED

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 100_000_000
    assert taker_account.cash_balance == -100_200_000_000

    maker_account = gateway.get_account_state("maker_1")
    assert maker_account.base_position == -100_000_000


def test_ioc_partially_fills_then_cancels_remainder() -> None:
    """
    Maker sells 50_000_000 at 1000.
    IOC buys 100_000_000 at 1000 — fills 50_000_000, remainder canceled.

    notional = 1000 * 50_000_000 = 50_000_000_000
    taker_fee = (50_000_000_000 * 20) // 10_000 = 100_000_000
    """
    gateway = ExchangeGateway()
    maker = Order(order_id="s1", owner_id="maker_1", side=Side.SELL, price=1000, quantity=50_000_000)
    gateway.submit_order(maker)

    ioc = Order(
        order_id="ioc1",
        owner_id="taker_1",
        side=Side.BUY,
        price=1000,
        quantity=100_000_000,
        tif=TimeInForce.IOC,
    )
    result = gateway.submit_order(ioc)

    assert result.accepted is True
    assert result.reason == "ioc_unfilled_canceled"

    assert len(result.fills) == 1
    assert result.fills[0].quantity == 50_000_000
    assert result.taker_fee == 100_000_000

    assert gateway.get_order("ioc1") is None

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 50_000_000


# ── Cancel ────────────────────────────────────────────────────────────────────

def test_cancel_resting_gtc_order_removes_it_from_book() -> None:
    """
    Submit a GTC buy, cancel it.
    Order must disappear from the book.
    Canceling the same order a second time must return False.
    """
    gateway = ExchangeGateway()
    order = Order(order_id="b1", side=Side.BUY, price=990, quantity=100_000_000)
    gateway.submit_order(order)

    # Order is on the book
    assert gateway.get_order("b1") is not None

    # First cancel succeeds
    assert gateway.cancel_order("b1") is True

    # Order is gone from the book
    assert gateway.get_order("b1") is None

    # Second cancel returns False — already gone
    assert gateway.cancel_order("b1") is False


# ── Price priority ────────────────────────────────────────────────────────────

def test_price_priority_best_ask_fills_first() -> None:
    """
    Fill at price 990:
    notional = 990 * 100_000_000 = 99_000_000_000
    taker_fee = (99_000_000_000 * 20) // 10_000 = 198_000_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="s_high", owner_id="m1", side=Side.SELL, price=1010, quantity=100_000_000))
    gateway.submit_order(Order(order_id="s_low",  owner_id="m2", side=Side.SELL, price=990,  quantity=100_000_000))

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1010, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1
    assert result.fills[0].maker_order_id == "s_low"
    assert result.fills[0].price == 990
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 198_000_000

    assert gateway.get_order("s_low") is None
    assert gateway.get_order("s_high") is not None

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 100_000_000
    assert taker_account.cash_balance == -(99_000_000_000 + 198_000_000)


def test_price_priority_best_bid_fills_first() -> None:
    """
    Fill at price 1010:
    notional = 1010 * 100_000_000 = 101_000_000_000
    taker_fee = (101_000_000_000 * 20) // 10_000 = 202_000_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="b_low",  owner_id="m1", side=Side.BUY, price=990,  quantity=100_000_000))
    gateway.submit_order(Order(order_id="b_high", owner_id="m2", side=Side.BUY, price=1010, quantity=100_000_000))

    taker = Order(order_id="s1", owner_id="taker_1", side=Side.SELL, price=990, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1
    assert result.fills[0].maker_order_id == "b_high"
    assert result.fills[0].price == 1010
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 202_000_000

    assert gateway.get_order("b_high") is None
    assert gateway.get_order("b_low") is not None

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == -100_000_000
    assert taker_account.cash_balance == 101_000_000_000 - 202_000_000


# ── Time priority ─────────────────────────────────────────────────────────────

def test_time_priority_earlier_order_fills_first() -> None:
    """
    notional = 1000 * 100_000_000 = 100_000_000_000
    taker_fee = (100_000_000_000 * 20) // 10_000 = 200_000_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="s_first",  owner_id="m1", side=Side.SELL, price=1000, quantity=100_000_000))
    gateway.submit_order(Order(order_id="s_second", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1
    assert result.fills[0].maker_order_id == "s_first"
    assert result.fills[0].price == 1000
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 200_000_000

    assert gateway.get_order("s_first") is None
    assert gateway.get_order("s_second") is not None


# ── POST_ONLY resting ─────────────────────────────────────────────────────────

def test_post_only_rests_on_book_when_no_cross() -> None:
    """
    Empty book — POST_ONLY buy at 990 cannot cross anything.
    Must be accepted and rest on the book like a normal GTC order.
    """
    gateway = ExchangeGateway()
    order = Order(
        order_id="po1",
        side=Side.BUY,
        price=990,
        quantity=100_000_000,
        tif=TimeInForce.POST_ONLY,
    )
    result = gateway.submit_order(order)

    assert result.accepted is True
    assert result.fills == []
    assert result.reason == ""

    resting = gateway.get_order("po1")
    assert resting is not None
    assert resting.remaining == 100_000_000


# ── Fee accounting ────────────────────────────────────────────────────────────

def test_fee_pool_accumulates_across_multiple_fills() -> None:
    """
    Trade 1 & 2: price=1000, qty=100_000_000 each.
    notional=100_000_000_000 per trade.
    taker_fee = 200_000_000, maker_fee = 100_000_000 per trade.
    Total fee_pool per trade = 300_000_000.
    """
    gateway = ExchangeGateway()

    gateway.submit_order(Order(order_id="s1", owner_id="m1", side=Side.SELL, price=1000, quantity=100_000_000))
    gateway.submit_order(Order(order_id="b1", owner_id="t1", side=Side.BUY,  price=1000, quantity=100_000_000))

    assert gateway.get_fee_pool() == 300_000_000

    gateway.submit_order(Order(order_id="s2", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))
    gateway.submit_order(Order(order_id="b2", owner_id="t2", side=Side.BUY,  price=1000, quantity=100_000_000))

    assert gateway.get_fee_pool() == 600_000_000


# ── Multi-fill taker ──────────────────────────────────────────────────────────

def test_taker_fills_across_two_price_levels() -> None:
    """
    Round 1 (s2@1000): notional=100_000_000_000, taker_fee=200M, maker_fee=100M
    Round 2 (s1@1010): notional=50_500_000_000, taker_fee=101M, maker_fee=50.5M
    Total taker_fee = 301_000_000
    Total fee_pool = 301_000_000 + 150_500_000 = 451_500_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="s1", owner_id="m1", side=Side.SELL, price=1010, quantity=100_000_000))
    gateway.submit_order(Order(order_id="s2", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1010, quantity=150_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 2

    assert result.fills[0].maker_order_id == "s2"
    assert result.fills[0].price == 1000
    assert result.fills[0].quantity == 100_000_000

    assert result.fills[1].maker_order_id == "s1"
    assert result.fills[1].price == 1010
    assert result.fills[1].quantity == 50_000_000

    # taker_fee: (100B * 20 // 10000) + (50.5B * 20 // 10000) = 200M + 101M = 301M
    assert result.taker_fee == 301_000_000

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 150_000_000

    # cash = -(100B + 200M) - (50.5B + 101M)
    assert taker_account.cash_balance == -(100_200_000_000 + 50_601_000_000)

    # fee_pool = taker(301M) + maker_s2(100M) + maker_s1(50.5M) = 451.5M
    assert gateway.get_fee_pool() == 451_500_000

    s1 = gateway.get_order("s1")
    assert s1 is not None
    assert s1.remaining == 50_000_000
    assert s1.status == OrderStatus.PARTIALLY_FILLED
    assert gateway.get_order("s2") is None