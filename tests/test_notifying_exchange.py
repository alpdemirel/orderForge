from unittest.mock import MagicMock
from exchange.models import ExecutionResult, Fill, Order, Side
from sim.notifying_exchange import NotifyingExchange

def test_notifying_exchange_emits_callbacks():
    # 1. Create mocks for the gateway and the callback function
    mock_gateway = MagicMock()
    mock_callback = MagicMock()

    # 2. Prepare a fake fill and an execution result
    fake_fill = Fill(
        maker_order_id="maker_1",
        taker_order_id="test_order_1",
        maker_owner_id="market",
        taker_owner_id="strategy",
        maker_side=Side.SELL,
        taker_side=Side.BUY,
        price=600000,
        quantity=100000000
    )
    mock_gateway.submit_order.return_value = ExecutionResult(
        accepted=True, fills=[fake_fill]
    )

    # 3. Initialize the adapter with the mocks
    adapter = NotifyingExchange(gateway=mock_gateway, on_fill=mock_callback)

    # 4. Submit a test order
    test_order = Order(
        order_id="test_order_1",
        side=Side.BUY,
        price=600000,
        quantity=100000000
    )
    result = adapter.submit_order(test_order)

    # 5. Assertions
    # Verify the order was forwarded to the gateway
    mock_gateway.submit_order.assert_called_once_with(test_order)

    # Verify the callback was triggered with the correct fill
    mock_callback.assert_called_once_with(fake_fill)

    # Verify the fill was recorded in the internal list
    assert len(adapter.emitted_fills) == 1
    assert adapter.emitted_fills[0] == fake_fill

    # Verify the original result was returned unmodified
    assert result.accepted is True
    assert result.fills == [fake_fill]