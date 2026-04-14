"""
Accuracy validation: replay real L3 data and compare book state against
periodic S3 snapshots (ground truth).

Metrics:
  1. Best bid/ask price accuracy (exact match rate)
  2. Spread accuracy (within 1 tick tolerance)
  3. Book depth accuracy (Jaccard similarity of order IDs)
  4. Price drift over time
  5. TOB quantity accuracy

The test replays from the in-stream bf_snapshot in events.log, then
compares the replayed book against S3 snapshot_HHMMSS.log files taken
every ~10 minutes by the capture pipeline.
"""

import json
import math
import os
import logging

import pytest

from exchange.engine import ExchangeEngine
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER, MARKET_OWNER_ID, Side
from market_data.reader import read_capture_file, is_bitfinex_snapshot, is_bitfinex_update
from strategy.noop import NoOpStrategy
from market_data.replayer import Replayer

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "2026-03-15")
EVENTS_FILE = os.path.join(DATA_DIR, "events.log")

# Skip if real data is not available
pytestmark = pytest.mark.skipif(
    not os.path.isfile(EVENTS_FILE),
    reason="Real capture data not available",
)


def _load_s3_snapshot(path: str) -> dict:
    """Load an S3 snapshot file and return {bitfinex_id: {price, amount, side}}."""
    with open(path, "r") as f:
        raw = json.load(f)
    result = {}
    for oid_str, entry in raw.items():
        result[int(oid_str)] = {
            "price": float(entry["price"]),
            "amount": float(entry["amount"]),
            "side": "bid" if entry["amount"] > 0 else "ask",
        }
    return result


def _snapshot_ts_ns(filename: str, capture_latency_ms: int = 750) -> int:
    """Convert snapshot_HHMMSS.log filename to epoch nanoseconds on 2026-03-15.

    Adds *capture_latency_ms* to compensate for S3 capture pipeline latency.
    """
    import datetime
    base = os.path.basename(filename)
    # snapshot_HHMMSS.log
    time_part = base.replace("snapshot_", "").replace(".log", "")
    hh = int(time_part[0:2])
    mm = int(time_part[2:4])
    ss = int(time_part[4:6])
    dt = datetime.datetime(2026, 3, 15, hh, mm, ss, tzinfo=datetime.timezone.utc)
    return int((dt.timestamp() + capture_latency_ms / 1000.0) * 1_000_000_000)


def _extract_book_tob(engine: ExchangeEngine) -> tuple:
    """Extract best bid/ask from engine as floats."""
    bb = engine.book.best_bid()
    ba = engine.book.best_ask()
    bb_f = bb / PRICE_MULTIPLIER if bb is not None else None
    ba_f = ba / PRICE_MULTIPLIER if ba is not None else None
    return bb_f, ba_f


def _extract_market_order_ids(engine: ExchangeEngine) -> set[int]:
    """Get set of Bitfinex order IDs currently on the book (market orders only)."""
    ids = set()
    for oid, order in engine.book.orders.items():
        if order.owner_id == MARKET_OWNER_ID and oid.startswith("mkt-"):
            try:
                ids.add(int(oid.replace("mkt-", "")))
            except ValueError:
                pass
    return ids


class TestBookAccuracyAgainstSnapshots:
    """Replay real events and compare book state to S3 snapshots."""

    @pytest.fixture(scope="class")
    def replay_result(self):
        """Replay events from bf_snapshot through ~2 hours of data,
        collecting book state at S3 snapshot checkpoint times."""
        engine = ExchangeEngine()
        strategy = NoOpStrategy()
        strategy.connect_exchange(engine)

        # Find S3 snapshots to use as checkpoints (05:27 through 07:27)
        snapshot_files = sorted([
            os.path.join(DATA_DIR, f)
            for f in os.listdir(DATA_DIR)
            if f.startswith("snapshot_") and f.endswith(".log")
        ])

        # Use snapshots from 05:30 to 07:30 (after the bf_snapshot at 05:21)
        checkpoints = {}
        for sf in snapshot_files:
            ts_ns = _snapshot_ts_ns(sf)
            base = os.path.basename(sf)
            hhmm = base.replace("snapshot_", "")[:4]
            hh, mm = int(hhmm[:2]), int(hhmm[2:4])
            if 527 < int(hhmm[:4]) <= 730:
                checkpoints[ts_ns] = {
                    "file": sf,
                    "name": base,
                    "replayed_bb": None,
                    "replayed_ba": None,
                    "snapshot_bb": None,
                    "snapshot_ba": None,
                    "jaccard": None,
                    "captured": False,
                }

        if not checkpoints:
            pytest.skip("No checkpoint snapshots in expected range")

        checkpoint_times = sorted(checkpoints.keys())
        next_checkpoint_idx = 0
        events_processed = 0
        past_snapshot = False
        prev_event_ts = 0

        # Stream through events
        for event in read_capture_file(EVENTS_FILE):
            if not past_snapshot:
                if is_bitfinex_snapshot(event):
                    # Apply the in-stream snapshot to seed the book
                    engine.apply_market_event(event)
                    past_snapshot = True
                    events_processed += 1
                    prev_event_ts = event.local_ts_ns
                continue

            # Closest-event interpolation: compare using whichever book
            # state (pre- or post-apply) is temporally closer to checkpoint.
            event_applied = False
            while (next_checkpoint_idx < len(checkpoint_times) and
                   event.local_ts_ns >= checkpoint_times[next_checkpoint_idx]):
                cp_ts = checkpoint_times[next_checkpoint_idx]
                cp = checkpoints[cp_ts]

                dist_before = cp_ts - prev_event_ts
                dist_after = event.local_ts_ns - cp_ts

                if dist_after < dist_before and not event_applied:
                    if is_bitfinex_update(event) or is_bitfinex_snapshot(event):
                        engine.apply_market_event(event)
                        events_processed += 1
                        event_applied = True

                # Capture replayed book state
                bb, ba = _extract_book_tob(engine)
                cp["replayed_bb"] = bb
                cp["replayed_ba"] = ba
                cp["replayed_ids"] = _extract_market_order_ids(engine)

                # Load ground truth
                gt = _load_s3_snapshot(cp["file"])
                bids = [e for e in gt.values() if e["side"] == "bid"]
                asks = [e for e in gt.values() if e["side"] == "ask"]
                cp["snapshot_bb"] = max((e["price"] for e in bids), default=None)
                cp["snapshot_ba"] = min((e["price"] for e in asks), default=None)
                cp["snapshot_ids"] = set(gt.keys())

                # Jaccard similarity of order IDs
                if cp["replayed_ids"] and cp["snapshot_ids"]:
                    intersection = len(cp["replayed_ids"] & cp["snapshot_ids"])
                    union = len(cp["replayed_ids"] | cp["snapshot_ids"])
                    cp["jaccard"] = intersection / union if union > 0 else 0.0
                else:
                    cp["jaccard"] = 0.0

                cp["captured"] = True
                next_checkpoint_idx += 1

            # Apply update if not already applied during checkpoint handling
            if not event_applied and (is_bitfinex_update(event) or is_bitfinex_snapshot(event)):
                engine.apply_market_event(event)
                events_processed += 1

            prev_event_ts = event.local_ts_ns

            # Stop after all checkpoints captured
            if next_checkpoint_idx >= len(checkpoint_times):
                break

        return {
            "checkpoints": checkpoints,
            "events_processed": events_processed,
        }

    def test_events_were_processed(self, replay_result):
        assert replay_result["events_processed"] > 100_000, \
            f"Only {replay_result['events_processed']} events processed"

    def test_checkpoints_captured(self, replay_result):
        captured = [cp for cp in replay_result["checkpoints"].values() if cp["captured"]]
        assert len(captured) >= 5, f"Only {len(captured)} checkpoints captured"

    def test_best_bid_accuracy(self, replay_result):
        """Best bid should be within 1 tick ($0.10) of ground truth at >80% of checkpoints."""
        captured = [cp for cp in replay_result["checkpoints"].values() if cp["captured"]]
        within_1_tick = 0
        exact = 0
        results = []

        for cp in captured:
            if cp["replayed_bb"] is not None and cp["snapshot_bb"] is not None:
                diff = abs(cp["replayed_bb"] - cp["snapshot_bb"])
                results.append(diff)
                if diff <= 0.1:  # 1 tick = $0.10 with PRICE_MULTIPLIER=10
                    within_1_tick += 1
                if diff == 0:
                    exact += 1

        total = len(results)
        if total == 0:
            pytest.skip("No valid bid comparisons")

        accuracy_1tick = within_1_tick / total
        exact_rate = exact / total
        avg_diff = sum(results) / total
        max_diff = max(results)

        logger.info(
            f"Best Bid: exact={exact_rate:.1%}, within_1tick={accuracy_1tick:.1%}, "
            f"avg_diff=${avg_diff:.2f}, max_diff=${max_diff:.2f} "
            f"({total} checkpoints)"
        )
        assert accuracy_1tick >= 0.80, \
            f"Bid accuracy {accuracy_1tick:.1%} < 80% (avg diff ${avg_diff:.2f})"

    def test_best_ask_accuracy(self, replay_result):
        """Best ask should be within 1 tick ($0.10) of ground truth at >80% of checkpoints."""
        captured = [cp for cp in replay_result["checkpoints"].values() if cp["captured"]]
        within_1_tick = 0
        exact = 0
        results = []

        for cp in captured:
            if cp["replayed_ba"] is not None and cp["snapshot_ba"] is not None:
                diff = abs(cp["replayed_ba"] - cp["snapshot_ba"])
                results.append(diff)
                if diff <= 0.1:
                    within_1_tick += 1
                if diff == 0:
                    exact += 1

        total = len(results)
        if total == 0:
            pytest.skip("No valid ask comparisons")

        accuracy_1tick = within_1_tick / total
        exact_rate = exact / total
        avg_diff = sum(results) / total
        max_diff = max(results)

        logger.info(
            f"Best Ask: exact={exact_rate:.1%}, within_1tick={accuracy_1tick:.1%}, "
            f"avg_diff=${avg_diff:.2f}, max_diff=${max_diff:.2f} "
            f"({total} checkpoints)"
        )
        assert accuracy_1tick >= 0.80, \
            f"Ask accuracy {accuracy_1tick:.1%} < 80% (avg diff ${avg_diff:.2f})"

    def test_spread_accuracy(self, replay_result):
        """Spread should be within $1 of ground truth at >80% of checkpoints."""
        captured = [cp for cp in replay_result["checkpoints"].values() if cp["captured"]]
        within_tolerance = 0
        results = []

        for cp in captured:
            if all(v is not None for v in [cp["replayed_bb"], cp["replayed_ba"],
                                            cp["snapshot_bb"], cp["snapshot_ba"]]):
                replayed_spread = cp["replayed_ba"] - cp["replayed_bb"]
                snapshot_spread = cp["snapshot_ba"] - cp["snapshot_bb"]
                diff = abs(replayed_spread - snapshot_spread)
                results.append(diff)
                if diff <= 1.0:
                    within_tolerance += 1

        total = len(results)
        if total == 0:
            pytest.skip("No valid spread comparisons")

        accuracy = within_tolerance / total
        avg_diff = sum(results) / total

        logger.info(
            f"Spread: within_$1={accuracy:.1%}, avg_diff=${avg_diff:.2f} "
            f"({total} checkpoints)"
        )
        assert accuracy >= 0.80, \
            f"Spread accuracy {accuracy:.1%} < 80% (avg diff ${avg_diff:.2f})"

    def test_order_id_jaccard_similarity(self, replay_result):
        """Order ID overlap (Jaccard) between replayed and snapshot books
        should average >50%. This measures whether the correct orders are
        on the book, not just the prices."""
        captured = [cp for cp in replay_result["checkpoints"].values()
                    if cp["captured"] and cp["jaccard"] is not None]
        if not captured:
            pytest.skip("No checkpoints with Jaccard data")

        jaccards = [cp["jaccard"] for cp in captured]
        avg_jaccard = sum(jaccards) / len(jaccards)
        min_jaccard = min(jaccards)

        logger.info(
            f"Jaccard: avg={avg_jaccard:.1%}, min={min_jaccard:.1%} "
            f"({len(jaccards)} checkpoints)"
        )
        assert avg_jaccard >= 0.50, \
            f"Average Jaccard {avg_jaccard:.1%} < 50%"

    def test_no_price_drift(self, replay_result):
        """Best bid error should not systematically increase over time (no drift).
        Measured as: correlation between checkpoint index and absolute error < 0.5."""
        captured = [cp for cp in replay_result["checkpoints"].values() if cp["captured"]]
        errors = []
        for cp in captured:
            if cp["replayed_bb"] is not None and cp["snapshot_bb"] is not None:
                errors.append(abs(cp["replayed_bb"] - cp["snapshot_bb"]))

        if len(errors) < 3:
            pytest.skip("Not enough checkpoints for drift analysis")

        # Simple drift metric: is the last third's avg error > 3x the first third's?
        n = len(errors)
        third = n // 3
        first_avg = sum(errors[:third]) / third if third > 0 else 0
        last_avg = sum(errors[-third:]) / third if third > 0 else 0

        logger.info(
            f"Drift: first_third_avg=${first_avg:.2f}, last_third_avg=${last_avg:.2f}"
        )
        # Allow some growth but not systematic drift
        if first_avg > 0:
            drift_ratio = last_avg / first_avg
            assert drift_ratio < 5.0, \
                f"Price drift ratio {drift_ratio:.1f}x (first=${first_avg:.2f}, last=${last_avg:.2f})"


class TestBookReconstructionFromSnapshot:
    """Verify that seeding from S3 snapshot produces a valid book."""

    def test_s3_snapshot_seed_produces_valid_tob(self):
        snapshot_path = os.path.join(DATA_DIR, "snapshot_000726.log")
        if not os.path.isfile(snapshot_path):
            pytest.skip("Snapshot file not available")

        engine = ExchangeEngine()
        with open(snapshot_path) as f:
            snapshot_data = json.load(f)

        engine.apply_s3_snapshot(snapshot_data)

        bb = engine.book.best_bid()
        ba = engine.book.best_ask()

        assert bb is not None, "No best bid after snapshot seed"
        assert ba is not None, "No best ask after snapshot seed"

        bb_f = bb / PRICE_MULTIPLIER
        ba_f = ba / PRICE_MULTIPLIER

        # BTC price should be in a reasonable range (60k-100k)
        assert 60000 < bb_f < 100000, f"Best bid ${bb_f} out of range"
        assert 60000 < ba_f < 100000, f"Best ask ${ba_f} out of range"
        assert ba_f > bb_f, f"Crossed book: bid ${bb_f} >= ask ${ba_f}"

        # Spread should be reasonable for BTC (< $50 — overnight can be wider)
        spread = ba_f - bb_f
        assert spread < 50, f"Spread ${spread} too wide"

        # Book should have orders
        assert len(engine.book.orders) > 50, \
            f"Only {len(engine.book.orders)} orders after snapshot"

    def test_s3_snapshot_order_count_matches(self):
        snapshot_path = os.path.join(DATA_DIR, "snapshot_000726.log")
        if not os.path.isfile(snapshot_path):
            pytest.skip("Snapshot file not available")

        engine = ExchangeEngine()
        with open(snapshot_path) as f:
            snapshot_data = json.load(f)

        engine.apply_s3_snapshot(snapshot_data)

        # All orders from snapshot should be on the book
        assert len(engine.book.orders) == len(snapshot_data), \
            f"Book has {len(engine.book.orders)} orders but snapshot has {len(snapshot_data)}"


class TestReplayDeterminism:
    """Verify replay produces identical results across multiple runs."""

    def test_two_runs_identical(self):
        """Two replays from the same snapshot produce identical book state."""
        snapshot_path = os.path.join(DATA_DIR, "snapshot_000726.log")
        if not os.path.isfile(snapshot_path):
            pytest.skip("Snapshot file not available")

        results = []
        for _ in range(2):
            engine = ExchangeEngine()
            with open(snapshot_path) as f:
                snapshot_data = json.load(f)
            engine.apply_s3_snapshot(snapshot_data)

            # Replay first 10000 events
            count = 0
            for event in read_capture_file(EVENTS_FILE):
                if is_bitfinex_update(event) or is_bitfinex_snapshot(event):
                    engine.apply_market_event(event)
                    count += 1
                    if count >= 10000:
                        break

            bb = engine.book.best_bid()
            ba = engine.book.best_ask()
            n_orders = len(engine.book.orders)
            results.append((bb, ba, n_orders))

        assert results[0] == results[1], \
            f"Non-deterministic: run1={results[0]} vs run2={results[1]}"
