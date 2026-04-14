"""
Validate that captured data contains real exchange timestamps.

Usage:
    python scripts/validate_exchange_timestamps.py data/2026-04-15/events.log

Reads the first N lines and reports:
  - How many have exchange_ts_ns > 0 vs == 0
  - Sample latencies (local_ts - exchange_ts)
  - Whether the data is usable for real latency emulation

Run this after deploying the TIMESTAMP flag (32768) to verify
the capture pipeline is producing dual-timestamp data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_data.reader import parse_line


def validate(filepath: str, max_lines: int = 10000):
    total = 0
    with_ts = 0
    without_ts = 0
    latencies_ms = []

    with open(filepath) as f:
        for line in f:
            if total >= max_lines:
                break
            event = parse_line(line)
            if event is None:
                continue
            total += 1

            if event.exchange_ts_ns and event.exchange_ts_ns > 0:
                with_ts += 1
                local_ms = event.local_ts_ns / 1_000_000
                exch_ms = event.exchange_ts_ns / 1_000_000
                latency = local_ms - exch_ms
                latencies_ms.append(latency)
            else:
                without_ts += 1

    print(f"File: {filepath}")
    print(f"Lines checked: {total}")
    print(f"  With exchange_ts:    {with_ts} ({with_ts/total*100:.1f}%)" if total else "")
    print(f"  Without exchange_ts: {without_ts} ({without_ts/total*100:.1f}%)" if total else "")

    if latencies_ms:
        latencies_ms.sort()
        n = len(latencies_ms)
        print(f"\nLatency distribution (local_ts - exchange_ts):")
        print(f"  Min:    {latencies_ms[0]:.1f} ms")
        print(f"  25th:   {latencies_ms[n//4]:.1f} ms")
        print(f"  Median: {latencies_ms[n//2]:.1f} ms")
        print(f"  75th:   {latencies_ms[3*n//4]:.1f} ms")
        print(f"  Max:    {latencies_ms[-1]:.1f} ms")
        print(f"  Mean:   {sum(latencies_ms)/n:.1f} ms")

        if latencies_ms[0] < 0:
            print(f"\n  WARNING: {sum(1 for l in latencies_ms if l < 0)} negative latencies detected (clock skew)")

        print(f"\nVerdict: REAL TIMESTAMPS - latency emulation will use per-tick values")
    else:
        print(f"\nVerdict: NO EXCHANGE TIMESTAMPS - will fall back to synthetic default_latency_ms")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <events.log>")
        sys.exit(1)
    validate(sys.argv[1])
