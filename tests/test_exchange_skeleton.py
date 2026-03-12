# Fixed-point convention: price * 10 ($100.0 → 1000), quantity * 100_000_000 (1 BTC → 100_000_000 satoshis).
# All accounting (cash_balance, base_position, fees, fee_pool) is in raw integer units.

from exchange import ExchangeGateway, Order, Side, TimeInForce


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
