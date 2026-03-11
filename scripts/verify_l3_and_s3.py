#!/usr/bin/env python3
"""
Verify L3 capture format and optionally test S3 upload.

Dual-timestamp format: <local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
  - exchange_ts_ns: stamped by exchange at event time (0 if not provided)
  - local_ts_ns: stamped by us at capture time

  python scripts/verify_l3_and_s3.py [path_to_events.log]
  python scripts/verify_l3_and_s3.py data/2026-03-11/events.log
  python scripts/verify_l3_and_s3.py --s3-test  # Test S3 upload (requires AWS creds)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_data.reader import parse_line, read_capture_file, extract_bitfinex_snapshot_orders, extract_bitfinex_update


def verify_l3_format(filepath: str) -> bool:
    """Verify file conforms to dual-timestamp L3 format. Returns True if valid."""
    errors = []
    count = 0
    for event in read_capture_file(filepath):
        count += 1
        if event.local_ts_ns <= 0:
            errors.append(f"Line {count}: local_ts_ns must be positive, got {event.local_ts_ns}")
        if event.exchange_ts_ns < 0:
            errors.append(f"Line {count}: exchange_ts_ns must be >= 0, got {event.exchange_ts_ns}")
        if not event.channel:
            errors.append(f"Line {count}: channel cannot be empty")

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return False
    print(f"OK: {count} events parsed, dual-timestamp L3 format valid")
    return True


def verify_bitfinex_l3(filepath: str) -> bool:
    """Verify Bitfinex L3 structure (snapshot + updates)."""
    snapshot_count = 0
    update_count = 0
    for event in read_capture_file(filepath):
        if event.channel == "bf_snapshot":
            orders = extract_bitfinex_snapshot_orders(event)
            if not orders:
                print(f"WARN: bf_snapshot with 0 orders")
            snapshot_count += 1
        elif event.channel == "bf_update":
            upd = extract_bitfinex_update(event)
            if upd is None:
                print(f"WARN: bf_update could not be parsed")
            update_count += 1

    if snapshot_count == 0 and update_count == 0:
        print("INFO: No bf_snapshot or bf_update events (may be bf_info/bf_conf only)")
    else:
        print(f"OK: {snapshot_count} snapshot(s), {update_count} update(s)")
    return True


def test_s3_upload(bucket: str, prefix: str) -> bool:
    """Test S3 upload with a small file. Returns True if success."""
    try:
        import boto3
    except ImportError:
        print("ERROR: boto3 not installed. pip install boto3")
        return False

    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "events_l3_sample.log"
    if not fixture.exists():
        print(f"ERROR: Fixture not found: {fixture}")
        return False

    key = f"{prefix}/verify_test/sample.log"
    try:
        s3 = boto3.client("s3")
        s3.upload_file(str(fixture), bucket, key)
        print(f"OK: Uploaded to s3://{bucket}/{key}")
        s3.delete_object(Bucket=bucket, Key=key)
        print("OK: Cleaned up test object")
        return True
    except Exception as e:
        print(f"ERROR: S3 test failed: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify L3 format and optionally test S3")
    parser.add_argument("filepath", nargs="?", default="tests/fixtures/events_l3_sample.log")
    parser.add_argument("--s3-test", action="store_true", help="Test S3 upload")
    args = parser.parse_args()

    filepath = args.filepath
    if not Path(filepath).exists():
        print(f"ERROR: File not found: {filepath}")
        return 1

    print(f"Verifying: {filepath}")
    if not verify_l3_format(filepath):
        return 1
    verify_bitfinex_l3(filepath)

    if args.s3_test:
        bucket = os.getenv("S3_BUCKET", "orderforge-s3-bucket")
        prefix = os.getenv("S3_PREFIX", "bitfinex/btcusd")
        print(f"\nTesting S3 upload to s3://{bucket}/{prefix}/...")
        if not test_s3_upload(bucket, prefix):
            return 1

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
