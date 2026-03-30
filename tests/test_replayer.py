"""Unit tests for the Replayer orchestrator."""

from unittest.mock import MagicMock, patch
from common.types import MarketEvent
from market_data.replayer import Replayer

@patch("market_data.replayer.read_capture_file")
def test_replayer_emits_market_data(mock_read_capture_file):
    # 1. Prepare a fake Bitfinex snapshot event
    # 1000000000 ns = 1000 ms. 900000000 ns = 900 ms.
    fake_snapshot = MarketEvent(
        local_ts_ns=1000000000,  
        exchange_ts_ns=900000000, 
        channel="bf_snapshot",
        raw_json=[1, [[111, 60000.0, 1.0], [222, 60005.0, -1.0]]] # Bid: 60000, Ask: 60005
    )
    
    # Force the reader to yield our fake snapshot
    mock_read_capture_file.return_value = [fake_snapshot]

    # 2. Initialize Replayer with a mock strategy
    mock_strategy = MagicMock()
    replayer = Replayer(filepath="dummy_path.log", strategy=mock_strategy)

    # 3. Run the orchestrator
    replayer.run()

    # 4. ASSERTIONS
    # A. Did it synchronize the clock correctly (ns to ms)?
    mock_strategy.update_clock.assert_called_once_with(900, 1000)

    # B. Did it extract the top of book and send floats to the strategy?
    expected_payload = {
        "type": "top_of_book",
        "best_bid": 60000.0,
        "best_ask": 60005.0,
        "mid_price": 60002.5,
        "local_ts_ms": 1000,
        "exchange_ts_ms": 900
    }
    mock_strategy.on_market_data.assert_called_once_with(expected_payload)