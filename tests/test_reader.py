import pytest
from common.types import MarketEvent
from market_data.exceptions import MalformedRecordError
from market_data.reader import parse_line, read_capture_file
import tempfile
import os

def test_parse_line_valid():
    line = '1772707035035967000|level2|{"type":"l2update","product_id":"BTC-USD"}'
    event = parse_line(line)
    assert event is not None
    assert event.local_ts_ns == 1772707035035967000
    assert event.channel == "level2"
    assert event.raw_json == {"type": "l2update", "product_id": "BTC-USD"}

def test_parse_line_missing_delimiter():
    line = '1772707035035967000level2|{"type":"l2update"}'
    with pytest.raises(MalformedRecordError):
        parse_line(line, strict=True)
    
    assert parse_line(line, strict=False) is None

def test_parse_line_invalid_json():
    line = '1772707035035967000|level2|{"type":"l2update",,}'
    with pytest.raises(MalformedRecordError):
        parse_line(line, strict=True)
        
    assert parse_line(line, strict=False) is None

def test_parse_line_empty():
    assert parse_line("") is None
    assert parse_line("   ") is None

def test_read_capture_file():
    lines = [
        '1772707035035900000|level2|{"type":"snapshot"}\n',
        'invalid_line_no_delimiters\n',
        '1772707035035967000|heartbeat|{"type":"heartbeat"}\n'
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.writelines(lines)
        temp_path = f.name
        
    try:
        # Test permissive mode (default)
        events = list(read_capture_file(temp_path))
        assert len(events) == 2
        assert events[0].channel == "level2"
        assert events[1].channel == "heartbeat"
        
        # Test strict mode
        with pytest.raises(MalformedRecordError):
            list(read_capture_file(temp_path, strict=True))
            
    finally:
        os.unlink(temp_path)
