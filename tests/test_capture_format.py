import json

from capture.main import build_line


def test_capture_line_matches_required_contract() -> None:
    """Dual-timestamp L3 format: <local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>"""
    channel = "level2"
    raw_json = '{"type":"snapshot","product_id":"BTC-USD"}'

    line = build_line(channel, raw_json)
    parts = line.split("|", 3)

    assert len(parts) == 4
    assert parts[0].isdigit()
    assert parts[1].isdigit()  # exchange_ts_ns (0 when not provided)
    assert parts[2] == channel
    assert json.loads(parts[3])["type"] == "snapshot"
