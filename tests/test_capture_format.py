import json

from capture.main import build_line, classify_bitfinex_msg


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


# ── classify_bitfinex_msg with TIME flag (mts extraction) ──────────────────


def test_classify_update_without_mts():
    """Standard update without TIME flag — exchange_ts_ns should be 0."""
    raw = json.dumps([12345, 1001, 43000.0, 0.5])
    label, out_json, ts = classify_bitfinex_msg(raw)
    assert label == "bf_update"
    assert ts == 0
    data = json.loads(out_json)
    assert len(data) == 4


def test_classify_update_with_mts():
    """Update with trailing mts (TIME flag enabled) — extract and strip."""
    mts = 1710000000000  # millisecond epoch
    raw = json.dumps([12345, 1001, 43000.0, 0.5, mts])
    label, out_json, ts = classify_bitfinex_msg(raw)
    assert label == "bf_update"
    assert ts == mts * 1_000_000  # converted to nanoseconds
    # mts should be stripped from output
    data = json.loads(out_json)
    assert len(data) == 4
    assert data == [12345, 1001, 43000.0, 0.5]


def test_classify_snapshot_with_mts():
    """Snapshot with trailing mts — extract and strip."""
    mts = 1710000000000
    raw = json.dumps([12345, [[1001, 43000.0, 0.5], [1002, 43001.0, -0.3]], mts])
    label, out_json, ts = classify_bitfinex_msg(raw)
    assert label == "bf_snapshot"
    assert ts == mts * 1_000_000
    data = json.loads(out_json)
    assert len(data) == 2  # mts stripped


def test_classify_heartbeat_with_mts():
    """Heartbeat with mts — [CHAN_ID, 'hb', MTS]."""
    mts = 1710000000000
    raw = json.dumps([12345, "hb", mts])
    label, _, ts = classify_bitfinex_msg(raw)
    assert label == "bf_heartbeat"
    assert ts == mts * 1_000_000


def test_classify_dict_event():
    """Dict events (info, subscribed) have no mts — ts should be 0."""
    raw = json.dumps({"event": "subscribed", "channel": "book"})
    label, _, ts = classify_bitfinex_msg(raw)
    assert label == "bf_subscribed"
    assert ts == 0


def test_classify_nested_update_with_mts():
    """Nested update format [CHAN_ID, [OID, P, A], MTS]."""
    mts = 1710000000000
    raw = json.dumps([12345, [1001, 43000.0, 0.5], mts])
    label, out_json, ts = classify_bitfinex_msg(raw)
    assert label == "bf_update"
    assert ts == mts * 1_000_000
    data = json.loads(out_json)
    assert len(data) == 2
    assert data[1] == [1001, 43000.0, 0.5]
