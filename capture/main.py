"""Week 1 capture stub that writes sample lines to a file."""

from __future__ import annotations

import sys
from pathlib import Path

from common.time_utils import now_local_ns


def build_line(channel: str, raw_json: str) -> str:
    return f"{now_local_ns()}|{channel}|{raw_json}"


def main() -> int:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("capture_stub_output.log")

    sample_messages = [
        ("level2", '{"type":"snapshot","product_id":"BTC-USD"}'),
        (
            "level2",
            '{"type":"l2update","product_id":"BTC-USD","changes":[["buy","30000.00","0.20"]]}',
        ),
        ("heartbeat", '{"type":"heartbeat","product_id":"BTC-USD"}'),
    ]

    with output_path.open("a", encoding="utf-8") as file_handle:
        for channel, raw_json in sample_messages:
            file_handle.write(build_line(channel, raw_json) + "\n")

    print(f"wrote {len(sample_messages)} sample lines to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
