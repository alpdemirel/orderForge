import pytest
from common.time_utils import now_local_ns
from capture.main import build_line

def test_build_line_format():
    """Test that the writer builds lines in the exact required format."""
    channel = "level2"
    raw_json = '{"type":"snapshot","product_id":"BTC-USD"}'
    
    # Store approximate time before calling
    before_ns = now_local_ns()
    
    result = build_line(channel, raw_json)
    
    # Store approximate time after calling
    after_ns = now_local_ns()
    
    # Check delimiters
    parts = result.split("|")
    assert len(parts) == 3, f"Expected 3 parts separated by '|', got {len(parts)}: {result}"
    
    # Check timestamp
    ts_str = parts[0]
    assert ts_str.isdigit(), f"Timestamp must be an integer string, got {ts_str}"
    
    ts_ns = int(ts_str)
    # The timestamp should be roughly between our before and after markers
    # We allow a small buffer in case of very fast execution or clock jitter
    assert before_ns <= ts_ns <= after_ns + 1_000_000, f"Timestamp {ts_ns} is outside expected range"
    
    # Check channel and JSON
    assert parts[1] == channel
    assert parts[2] == raw_json
    
    # Check exact reconstruction
    expected_suffix = f"|{channel}|{raw_json}"
    assert result.endswith(expected_suffix)
