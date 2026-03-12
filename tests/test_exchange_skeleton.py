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
    Taker fully fills (remaining=0), maker still has 100_000_000 left on book.

    notional = 1000 * 100_000_000 = 100_000_000_000
    taker_fee = (100_000_000_000 * 5) // 10_000 = 50_000_000
    taker cash_balance = -(100_000_000_000 + 50_000_000) = -100_050_000_000
    """
    gateway = ExchangeGateway()
    maker = Order(order_id="s1", owner_id="maker_1", side=Side.SELL, price=1000, quantity=200_000_000)
    gateway.submit_order(maker)

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(taker)

    # Taker fully filled
    assert result.accepted is True
    assert len(result.fills) == 1
    assert result.fills[0].price == 1000
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 50_000_000

    # Taker is gone from book (fully filled)
    assert gateway.get_order("b1") is None

    # Maker still rests with half remaining
    resting_maker = gateway.get_order("s1")
    assert resting_maker is not None
    assert resting_maker.remaining == 100_000_000
    assert resting_maker.status == OrderStatus.PARTIALLY_FILLED

    # Account positions
    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 100_000_000
    assert taker_account.cash_balance == -100_050_000_000

    maker_account = gateway.get_account_state("maker_1")
    assert maker_account.base_position == -100_000_000


def test_ioc_partially_fills_then_cancels_remainder() -> None:
    """
    Maker sells 50_000_000 at 1000.
    IOC buys 100_000_000 at 1000 — fills 50_000_000, remainder canceled.

    notional = 1000 * 50_000_000 = 50_000_000_000
    taker_fee = (50_000_000_000 * 5) // 10_000 = 25_000_000
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

    # Accepted but canceled after partial fill
    assert result.accepted is True
    assert result.reason == "ioc_unfilled_canceled"

    # One fill happened
    assert len(result.fills) == 1
    assert result.fills[0].quantity == 50_000_000
    assert result.taker_fee == 25_000_000

    # IOC order is not on the book (was canceled)
    assert gateway.get_order("ioc1") is None

    # Taker got 50_000_000 BTC
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
    Two sell orders: s_low at price 990, s_high at price 1010.
    Taker buys at 1010, quantity 100_000_000.
    Engine must fill s_low first (lowest ask = best price for buyer).

    Fill at price 990:
    notional = 990 * 100_000_000 = 99_000_000_000
    taker_fee = (99_000_000_000 * 5) // 10_000 = 49_500_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="s_high", owner_id="m1", side=Side.SELL, price=1010, quantity=100_000_000))
    gateway.submit_order(Order(order_id="s_low",  owner_id="m2", side=Side.SELL, price=990,  quantity=100_000_000))

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1010, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1

    # Must have filled the cheaper seller
    assert result.fills[0].maker_order_id == "s_low"
    assert result.fills[0].price == 990
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 49_500_000

    # s_low is gone, s_high still rests
    assert gateway.get_order("s_low") is None
    assert gateway.get_order("s_high") is not None

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 100_000_000
    assert taker_account.cash_balance == -99_049_500_000


def test_price_priority_best_bid_fills_first() -> None:
    """
    Two buy orders: b_high at price 1010, b_low at price 990.
    Taker sells at 990, quantity 100_000_000.
    Engine must fill b_high first (highest bid = best price for seller).

    Fill at price 1010:
    notional = 1010 * 100_000_000 = 101_000_000_000
    taker_fee = (101_000_000_000 * 5) // 10_000 = 50_500_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="b_low",  owner_id="m1", side=Side.BUY, price=990,  quantity=100_000_000))
    gateway.submit_order(Order(order_id="b_high", owner_id="m2", side=Side.BUY, price=1010, quantity=100_000_000))

    taker = Order(order_id="s1", owner_id="taker_1", side=Side.SELL, price=990, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1

    # Must have filled the higher buyer
    assert result.fills[0].maker_order_id == "b_high"
    assert result.fills[0].price == 1010
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 50_500_000

    # b_high is gone, b_low still rests
    assert gateway.get_order("b_high") is None
    assert gateway.get_order("b_low") is not None

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == -100_000_000
    assert taker_account.cash_balance == 101_000_000_000 - 50_500_000


# ── Time priority ─────────────────────────────────────────────────────────────

def test_time_priority_earlier_order_fills_first() -> None:
    """
    Two sell orders at the same price 1000: s_first submitted before s_second.
    Taker buys exactly 100_000_000 — only enough to fill one order.
    s_first must fill (FIFO queue at the price level).

    notional = 1000 * 100_000_000 = 100_000_000_000
    taker_fee = (100_000_000_000 * 5) // 10_000 = 50_000_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="s_first",  owner_id="m1", side=Side.SELL, price=1000, quantity=100_000_000))
    gateway.submit_order(Order(order_id="s_second", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1000, quantity=100_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 1

    # Earlier order must fill first
    assert result.fills[0].maker_order_id == "s_first"
    assert result.fills[0].price == 1000
    assert result.fills[0].quantity == 100_000_000
    assert result.taker_fee == 50_000_000

    # s_first gone, s_second still rests
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
    Two separate trades. Fee pool must be sum of all taker fees.

    Trade 1: price=1000, qty=100_000_000
      notional=100_000_000_000, taker_fee=50_000_000

    Trade 2: price=1000, qty=100_000_000
      notional=100_000_000_000, taker_fee=50_000_000

    Total fee_pool = 100_000_000 (maker fee is 0 throughout)
    """
    gateway = ExchangeGateway()

    # Trade 1
    gateway.submit_order(Order(order_id="s1", owner_id="m1", side=Side.SELL, price=1000, quantity=100_000_000))
    gateway.submit_order(Order(order_id="b1", owner_id="t1", side=Side.BUY,  price=1000, quantity=100_000_000))

    assert gateway.get_fee_pool() == 50_000_000

    # Trade 2
    gateway.submit_order(Order(order_id="s2", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))
    gateway.submit_order(Order(order_id="b2", owner_id="t2", side=Side.BUY,  price=1000, quantity=100_000_000))

    assert gateway.get_fee_pool() == 100_000_000


# ── Multi-fill taker ──────────────────────────────────────────────────────────

def test_taker_fills_across_two_price_levels() -> None:
    """
    s1 at price 1010, qty 100_000_000
    s2 at price 1000, qty 100_000_000
    Buyer at 1010, qty 150_000_000 — fills s2 first, then partial s1.

    Round 1 fill (s2 at 1000):
      notional = 1000 * 100_000_000 = 100_000_000_000
      fee = (100_000_000_000 * 5) // 10_000 = 50_000_000

    Round 2 fill (s1 at 1010):
      notional = 1010 * 50_000_000 = 50_500_000_000
      fee = (50_500_000_000 * 5) // 10_000 = 25_250_000

    Total taker_fee = 75_250_000
    Total base_position = 150_000_000
    Total cash_balance = -(100_000_000_000 + 50_000_000 + 50_500_000_000 + 25_250_000)
                       = -150_575_250_000
    fee_pool = 75_250_000
    """
    gateway = ExchangeGateway()
    gateway.submit_order(Order(order_id="s1", owner_id="m1", side=Side.SELL, price=1010, quantity=100_000_000))
    gateway.submit_order(Order(order_id="s2", owner_id="m2", side=Side.SELL, price=1000, quantity=100_000_000))

    taker = Order(order_id="b1", owner_id="taker_1", side=Side.BUY, price=1010, quantity=150_000_000)
    result = gateway.submit_order(taker)

    assert result.accepted is True
    assert len(result.fills) == 2

    # First fill must be the cheaper maker (price priority)
    assert result.fills[0].maker_order_id == "s2"
    assert result.fills[0].price == 1000
    assert result.fills[0].quantity == 100_000_000

    # Second fill is the more expensive maker
    assert result.fills[1].maker_order_id == "s1"
    assert result.fills[1].price == 1010
    assert result.fills[1].quantity == 50_000_000

    assert result.taker_fee == 75_250_000

    taker_account = gateway.get_account_state("taker_1")
    assert taker_account.base_position == 150_000_000
    assert taker_account.cash_balance == -150_575_250_000

    assert gateway.get_fee_pool() == 75_250_000

    # s1 partially filled, 50_000_000 still rests
    s1 = gateway.get_order("s1")
    assert s1 is not None
    assert s1.remaining == 50_000_000
    assert s1.status == OrderStatus.PARTIALLY_FILLED

    # s2 fully consumed
    assert gateway.get_order("s2") is None