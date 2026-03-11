import pytest
from common.time_utils import now_local_ns
from capture.main import build_line

def test_build_line_format():
    """Test dual-timestamp L3 format: <local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>"""
    channel = "level2"
    raw_json = '{"type":"snapshot","product_id":"BTC-USD"}'

    # Store approximate time before calling
    before_ns = now_local_ns()

    result = build_line(channel, raw_json)

    # Store approximate time after calling
    after_ns = now_local_ns()

    # Check delimiters (4 parts)
    parts = result.split("|", 3)
    assert len(parts) == 4, f"Expected 4 parts (dual-timestamp), got {len(parts)}: {result}"

    # Check local timestamp
    ts_str = parts[0]
    assert ts_str.isdigit(), f"local_ts_ns must be integer string, got {ts_str}"
    ts_ns = int(ts_str)
    assert before_ns <= ts_ns <= after_ns + 1_000_000, f"Timestamp {ts_ns} outside expected range"

    # Check exchange_ts_ns (0 when not provided by exchange)
    assert parts[1].lstrip("-").isdigit(), f"exchange_ts_ns must be numeric, got {parts[1]}"

    # Check channel and JSON
    assert parts[2] == channel
    assert parts[3] == raw_json

    # Check exact reconstruction
    expected_suffix = f"|{parts[1]}|{channel}|{raw_json}"
    assert result.endswith(expected_suffix)
