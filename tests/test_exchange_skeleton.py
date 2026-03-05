from exchange import ExchangeGateway, Order, Side, TimeInForce


def test_post_only_rejected_when_crossing() -> None:
    gateway = ExchangeGateway()
    sell = Order(order_id="s1", side=Side.SELL, price=100.0, quantity=1.0)
    gateway.submit_order(sell)

    buy_post_only = Order(
        order_id="b1",
        side=Side.BUY,
        price=100.0,
        quantity=1.0,
        tif=TimeInForce.POST_ONLY,
    )
    result = gateway.submit_order(buy_post_only)

    assert result.accepted is False
    assert result.reason == "post_only_would_cross"


def test_ioc_unfilled_is_canceled() -> None:
    gateway = ExchangeGateway()
    ioc = Order(order_id="ioc1", side=Side.BUY, price=100.0, quantity=1.0, tif=TimeInForce.IOC)
    result = gateway.submit_order(ioc)

    assert result.accepted is True
    assert result.reason == "ioc_unfilled_canceled"
