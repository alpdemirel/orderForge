import json

from capture.main import build_line


def test_capture_line_matches_required_contract() -> None:
    channel = "level2"
    raw_json = '{"type":"snapshot","product_id":"BTC-USD"}'

    line = build_line(channel, raw_json)
    parts = line.split("|", 2)

    assert len(parts) == 3
    assert parts[0].isdigit()
    assert parts[1] == channel
    assert json.loads(parts[2])["type"] == "snapshot"
