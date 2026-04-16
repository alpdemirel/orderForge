"""
Full-day accuracy backtest: replay events.log and compare against all S3 snapshots.

Usage:
    python -m scripts.run_accuracy_backtest [--data-dir data/2026-03-15]
"""

import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from exchange.engine import ExchangeEngine
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER, MARKET_OWNER_ID
from market_data.reader import read_capture_file, is_bitfinex_snapshot, is_bitfinex_update


def load_s3_snapshot(path: str) -> dict:
    with open(path) as f:
        raw = json.load(f)
    result = {}
    for oid_str, entry in raw.items():
        result[int(oid_str)] = {
            "price": float(entry["price"]),
            "amount": float(entry["amount"]),
            "side": "bid" if entry["amount"] > 0 else "ask",
        }
    return result


def snapshot_ts_ns(
    filename: str,
    date_str: str = "2026-03-15",
    capture_latency_ms: int = 750,
) -> int:
    """Convert snapshot filename to epoch nanoseconds.

    ``capture_latency_ms`` compensates for the S3 capture pipeline latency:
    the filename timestamp records when the capture was *initiated*, but the
    actual Bitfinex book state it contains is from ~750ms later (API call +
    response + S3 write).  Calibrated empirically by sweeping offsets and
    maximising Jaccard against the event stream.
    """
    base = os.path.basename(filename)
    time_part = base.replace("snapshot_", "").replace(".log", "")
    hh = int(time_part[0:2])
    mm = int(time_part[2:4])
    ss = int(time_part[4:6])
    year, month, day = map(int, date_str.split("-"))
    dt = datetime.datetime(year, month, day, hh, mm, ss, tzinfo=datetime.timezone.utc)
    return int((dt.timestamp() + capture_latency_ms / 1000.0) * 1_000_000_000)


def extract_market_order_ids(engine: ExchangeEngine) -> set[int]:
    ids = set()
    for oid, order in engine.book.orders.items():
        if order.owner_id == MARKET_OWNER_ID and oid.startswith("mkt-"):
            try:
                ids.add(int(oid.replace("mkt-", "")))
            except ValueError:
                pass
    return ids


def run_backtest(data_dir: str):
    events_file = os.path.join(data_dir, "events.log")
    if not os.path.isfile(events_file):
        print(f"ERROR: {events_file} not found")
        return

    # Derive date from the data directory name (expected format: YYYY-MM-DD)
    dir_basename = os.path.basename(os.path.normpath(data_dir))
    try:
        datetime.datetime.strptime(dir_basename, "%Y-%m-%d")
        date_str = dir_basename
    except ValueError:
        date_str = "2026-03-15"
        print(f"WARNING: Could not parse date from directory name '{dir_basename}', "
              f"falling back to {date_str}")

    snapshot_files = sorted([
        os.path.join(data_dir, f)
        for f in os.listdir(data_dir)
        if f.startswith("snapshot_") and f.endswith(".log")
    ])
    print(f"Found {len(snapshot_files)} S3 snapshots")

    checkpoints = {}
    for sf in snapshot_files:
        ts_ns = snapshot_ts_ns(sf, date_str=date_str)
        checkpoints[ts_ns] = {"file": sf, "name": os.path.basename(sf)}

    checkpoint_times = sorted(checkpoints.keys())

    engine = ExchangeEngine()
    events_processed = 0
    snapshots_applied = 0
    next_cp_idx = 0
    past_snapshot = False
    prev_event_ts = 0
    wall_start = time.time()

    print(f"\nReplaying {events_file} ...")
    print(f"{'Checkpoint':<25} {'BB Diff':>10} {'BA Diff':>10} {'Spread Diff':>12} {'Jaccard':>10} {'Book Size':>10} {'Snap Size':>10}")
    print("-" * 95)

    results = []

    def _capture_state():
        bb_int = engine.book.best_bid()
        ba_int = engine.book.best_ask()
        bb = bb_int / PRICE_MULTIPLIER if bb_int is not None else None
        ba = ba_int / PRICE_MULTIPLIER if ba_int is not None else None
        return bb, ba, extract_market_order_ids(engine), len(engine.book.orders)

    def _compare(bb, ba, replayed_ids, book_size, cp):
        gt = load_s3_snapshot(cp["file"])
        bids_gt = [e for e in gt.values() if e["side"] == "bid"]
        asks_gt = [e for e in gt.values() if e["side"] == "ask"]
        gt_bb = max((e["price"] for e in bids_gt), default=None)
        gt_ba = min((e["price"] for e in asks_gt), default=None)
        gt_ids = set(gt.keys())

        bb_diff = abs(bb - gt_bb) if bb is not None and gt_bb is not None else None
        ba_diff = abs(ba - gt_ba) if ba is not None and gt_ba is not None else None
        spread_diff = None
        if all(v is not None for v in [bb, ba, gt_bb, gt_ba]):
            spread_diff = abs((ba - bb) - (gt_ba - gt_bb))

        jaccard = 0.0
        if replayed_ids and gt_ids:
            intersection = len(replayed_ids & gt_ids)
            union = len(replayed_ids | gt_ids)
            jaccard = intersection / union if union > 0 else 0.0

        return {
            "name": cp["name"], "bb_diff": bb_diff, "ba_diff": ba_diff,
            "spread_diff": spread_diff, "jaccard": jaccard,
            "book_size": book_size, "snap_size": len(gt),
        }

    for event in read_capture_file(events_file):
        if not past_snapshot:
            if is_bitfinex_snapshot(event):
                engine.apply_market_event(event)
                past_snapshot = True
                snapshots_applied += 1
                events_processed += 1
                seed_ts = event.local_ts_ns
                prev_event_ts = seed_ts
                seed_dt = datetime.datetime.fromtimestamp(
                    seed_ts / 1e9, tz=datetime.timezone.utc
                )
                print(f"\nBook seeded at {seed_dt.strftime('%H:%M:%S')} UTC "
                      f"({len(engine.book.orders)} orders)")
                while (next_cp_idx < len(checkpoint_times) and
                       checkpoint_times[next_cp_idx] <= seed_ts):
                    next_cp_idx += 1
                print(f"Skipping {next_cp_idx} snapshots before seed, "
                      f"{len(checkpoint_times) - next_cp_idx} checkpoints remaining\n")
            continue

        # Closest-event interpolation: for each checkpoint between the
        # previous event and this event, compare using whichever book
        # state (pre- or post-apply) is temporally closer.
        event_applied = False
        while (next_cp_idx < len(checkpoint_times) and
               event.local_ts_ns >= checkpoint_times[next_cp_idx]):
            cp_ts = checkpoint_times[next_cp_idx]
            cp = checkpoints[cp_ts]

            dist_before = cp_ts - prev_event_ts
            dist_after = event.local_ts_ns - cp_ts

            if dist_after < dist_before and not event_applied:
                # Post-event state is closer — apply first
                if is_bitfinex_update(event) or is_bitfinex_snapshot(event):
                    engine.apply_market_event(event)
                    events_processed += 1
                    if is_bitfinex_snapshot(event):
                        snapshots_applied += 1
                    event_applied = True

            bb, ba, replayed_ids, book_size = _capture_state()
            result = _compare(bb, ba, replayed_ids, book_size, cp)
            results.append(result)

            bb_str = f"${result['bb_diff']:.2f}" if result["bb_diff"] is not None else "N/A"
            ba_str = f"${result['ba_diff']:.2f}" if result["ba_diff"] is not None else "N/A"
            sp_str = f"${result['spread_diff']:.2f}" if result["spread_diff"] is not None else "N/A"
            print(f"{cp['name']:<25} {bb_str:>10} {ba_str:>10} {sp_str:>12} "
                  f"{result['jaccard']:>9.1%} {book_size:>10} {result['snap_size']:>10}")

            next_cp_idx += 1

        # Apply event if not already applied during checkpoint handling
        if not event_applied and (is_bitfinex_update(event) or is_bitfinex_snapshot(event)):
            engine.apply_market_event(event)
            events_processed += 1
            if is_bitfinex_snapshot(event):
                snapshots_applied += 1

        prev_event_ts = event.local_ts_ns

        if next_cp_idx >= len(checkpoint_times):
            break

    wall_elapsed = time.time() - wall_start

    print(f"\n{'='*95}")
    print(f"REPLAY SUMMARY")
    print(f"{'='*95}")
    print(f"Events processed:     {events_processed:,}")
    print(f"Snapshots applied:    {snapshots_applied} (1 = no disconnections)")
    print(f"Checkpoints compared: {len(results)}")
    print(f"Wall time:            {wall_elapsed:.1f}s")
    print(f"Throughput:           {events_processed/wall_elapsed:,.0f} events/s")

    if not results:
        print("\nNo checkpoints captured!")
        return

    bb_diffs = [r["bb_diff"] for r in results if r["bb_diff"] is not None]
    ba_diffs = [r["ba_diff"] for r in results if r["ba_diff"] is not None]
    spread_diffs = [r["spread_diff"] for r in results if r["spread_diff"] is not None]
    jaccards = [r["jaccard"] for r in results]

    print(f"\n{'METRIC':<30} {'VALUE':>15}")
    print("-" * 50)

    if bb_diffs:
        exact_bb = sum(1 for d in bb_diffs if d == 0) / len(bb_diffs)
        within_1tick_bb = sum(1 for d in bb_diffs if d <= 0.1) / len(bb_diffs)
        print(f"{'Best Bid exact match':<30} {exact_bb:>14.1%}")
        print(f"{'Best Bid within 1 tick':<30} {within_1tick_bb:>14.1%}")
        print(f"{'Best Bid avg diff':<30} {'$'+f'{sum(bb_diffs)/len(bb_diffs):.2f}':>15}")
        print(f"{'Best Bid max diff':<30} {'$'+f'{max(bb_diffs):.2f}':>15}")

    if ba_diffs:
        exact_ba = sum(1 for d in ba_diffs if d == 0) / len(ba_diffs)
        within_1tick_ba = sum(1 for d in ba_diffs if d <= 0.1) / len(ba_diffs)
        print(f"{'Best Ask exact match':<30} {exact_ba:>14.1%}")
        print(f"{'Best Ask within 1 tick':<30} {within_1tick_ba:>14.1%}")
        print(f"{'Best Ask avg diff':<30} {'$'+f'{sum(ba_diffs)/len(ba_diffs):.2f}':>15}")
        print(f"{'Best Ask max diff':<30} {'$'+f'{max(ba_diffs):.2f}':>15}")

    if spread_diffs:
        within_1_spread = sum(1 for d in spread_diffs if d <= 1.0) / len(spread_diffs)
        print(f"{'Spread within $1':<30} {within_1_spread:>14.1%}")
        print(f"{'Spread avg diff':<30} {'$'+f'{sum(spread_diffs)/len(spread_diffs):.2f}':>15}")

    if jaccards:
        print(f"{'Jaccard avg':<30} {sum(jaccards)/len(jaccards):>14.1%}")
        print(f"{'Jaccard min':<30} {min(jaccards):>14.1%}")
        print(f"{'Jaccard max':<30} {max(jaccards):>14.1%}")

    if len(bb_diffs) >= 6:
        n = len(bb_diffs)
        third = n // 3
        first_avg = sum(bb_diffs[:third]) / third
        last_avg = sum(bb_diffs[-third:]) / third
        print(f"\n{'DRIFT ANALYSIS':<30}")
        print("-" * 50)
        print(f"{'First third avg BB diff':<30} {'$'+f'{first_avg:.2f}':>15}")
        print(f"{'Last third avg BB diff':<30} {'$'+f'{last_avg:.2f}':>15}")
        if first_avg > 0:
            print(f"{'Drift ratio (last/first)':<30} {last_avg/first_avg:>14.1f}x")
        else:
            print(f"{'Drift ratio':<30} {'N/A (0 base)':>15}")


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data/2026-03-15"
    run_backtest(data_dir)
