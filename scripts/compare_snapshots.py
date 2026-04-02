#!/usr/bin/env python3
"""
Diagnostic framework to compare reconstructed best bid/ask prices against
stored snapshots at regular intervals to ensure L3 order book accuracy.
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple

# Ensure we can import from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.types import MarketEvent
from market_data.reader import (
    read_capture_file,
    is_bitfinex_snapshot,
    is_bitfinex_update,
    extract_bitfinex_snapshot_orders,
    extract_bitfinex_update
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def parse_snapshot_time(day_dir: Path, snap_path: Path) -> int:
    """
    Given a snapshot file like 'snapshot_142030.log', extract HHMMSS
    and combine with day_dir.name (e.g. '2026-10-23') to get nanoseconds.
    """
    # Try multiple heuristic splits, assuming standard format snapshot_HHMMSS.log
    parts = snap_path.stem.split("_")
    time_str = parts[-1]  # '142030'
    
    # If time_str doesn't look like HHMMSS, default to 0 to avoid crash,
    # but normally we assume rigid structure.
    try:
        date_str = day_dir.name.split("_")[-1] if "_" in day_dir.name else day_dir.name
        # Just safely grab the date like "2024-05-15"
        snap_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc)
        return int(snap_time.timestamp() * 1_000_000_000)
    except ValueError as e:
        logger.warning(f"Could not parse time from {snap_path.name} using {day_dir.name}. Error: {e}")
        return 0


def get_actual_top_of_book(snapshot_path: Path) -> Tuple[float, float]:
    """
    Reads a JSON snapshot and extracts the actual best bid and best ask.
    Format: {"order_id": {"price": 73988, "amount": 0.0024}, ...}
    Positive amount = Bid, Negative amount = Ask
    """
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        bids = []
        asks = []
        for order_id, info in data.items():
            price = float(info["price"])
            amount = float(info["amount"])
            
            if amount > 0:
                bids.append(price)
            elif amount < 0:
                asks.append(price)
                
        best_bid = max(bids) if bids else 0.0
        best_ask = min(asks) if asks else float('inf')
        return best_bid, best_ask
        
    except Exception as e:
        logger.error(f"Failed to read/parse snapshot {snapshot_path}: {e}")
        return 0.0, float('inf')


def main():
    parser = argparse.ArgumentParser(description="Snapshot Comparison Framework for L3 Book Accuracy")
    parser.add_argument("data_dir", type=str, help="Path to the daily data directory (e.g., capture/2024-05-15)")
    parser.add_argument("--tolerance", type=float, default=0.0001, help="Price tolerance for float comparison")
    args = parser.parse_args()

    day_dir = Path(args.data_dir)
    if not day_dir.exists() or not day_dir.is_dir():
        logger.error(f"Directory not found: {day_dir}")
        sys.exit(1)

    events_log = day_dir / "events.log"
    if not events_log.exists():
        logger.error(f"events.log not found in {day_dir}")
        sys.exit(1)

    # Find all snapshot files
    snapshot_files = list(day_dir.glob("snapshot_*.log")) + list(day_dir.glob("snapshot_*.json"))
    if not snapshot_files:
        logger.error(f"No snapshot files found in {day_dir}")
        sys.exit(1)

    # Parse and sort snapshots by time
    snapshots = []
    for s in snapshot_files:
        ns = parse_snapshot_time(day_dir, s)
        if ns > 0:
            snapshots.append((ns, s))
            
    snapshots.sort(key=lambda x: x[0])
    logger.info(f"Found {len(snapshots)} snapshots to verify against.")

    snapshot_idx = 0
    total_comparisons = 0
    mismatches = 0

    # Internal Order Book State
    bids: Dict[int, float] = {}
    asks: Dict[int, float] = {}

    def get_reconstructed_top() -> Tuple[float, float]:
        best_bid = max(bids.values()) if bids else 0.0
        best_ask = min(asks.values()) if asks else float('inf')
        return best_bid, best_ask

    logger.info("Starting events replay for reconstruction...")
    
    # Process events.log
    for event in read_capture_file(str(events_log), strict=False):
        
        # Check if we crossed the timestamp of the next snapshot (strict greater so we include events at the exact snap time)
        while snapshot_idx < len(snapshots) and event.local_ts_ns > snapshots[snapshot_idx][0]:
            snap_ns, snap_path = snapshots[snapshot_idx]
            
            # --- Checkpoint Evaluation ---
            recon_bid, recon_ask = get_reconstructed_top()
            actual_bid, actual_ask = get_actual_top_of_book(snap_path)
            
            total_comparisons += 1
            
            # Allow minor floating point drift, though they should ideally be exact
            bid_match = abs(recon_bid - actual_bid) <= args.tolerance
            ask_match = abs(recon_ask - actual_ask) <= args.tolerance
            
            if bid_match and ask_match:
                logger.info(f"[√] MATCH at {snap_path.name} | Bid: {actual_bid} | Ask: {actual_ask}")
            else:
                mismatches += 1
                logger.error(f"[X] MISMATCH at {snap_path.name} (Event NS: {event.local_ts_ns})")
                if not bid_match:
                    logger.error(f"    Bid: Reconstructed = {recon_bid}, Actual = {actual_bid}")
                if not ask_match:
                    logger.error(f"    Ask: Reconstructed = {recon_ask}, Actual = {actual_ask}")
            
            snapshot_idx += 1

        # Now apply the event to our internal book
        if is_bitfinex_snapshot(event):
            orders = extract_bitfinex_snapshot_orders(event)
            bids.clear()
            asks.clear()
            for order in orders:
                if order["side"] == "bid":
                    bids[order["order_id"]] = order["price"]
                else:
                    asks[order["order_id"]] = order["price"]

        elif is_bitfinex_update(event):
            update = extract_bitfinex_update(event)
            if update:
                order_id = update["order_id"]
                if update["removed"]:
                    bids.pop(order_id, None)
                    asks.pop(order_id, None)
                else:
                    if update["side"] == "bid":
                        bids[order_id] = update["price"]
                        asks.pop(order_id, None)
                    elif update["side"] == "ask":
                        asks[order_id] = update["price"]
                        bids.pop(order_id, None)

    # Note: If there are remaining snapshots after events end, we log them.
    while snapshot_idx < len(snapshots):
        _, snap_path = snapshots[snapshot_idx]
        logger.warning(f"Snapshot {snap_path.name} is beyond the end of events.log")
        snapshot_idx += 1

    # Final Report
    logger.info("=== RECONSTRUCTION VERIFICATION REPORT ===")
    logger.info(f"Total Snapshots Evaluated : {total_comparisons}")
    logger.info(f"Perfect Matches           : {total_comparisons - mismatches}")
    logger.info(f"Mismatches (Drift)        : {mismatches}")
    
    if total_comparisons > 0:
        accuracy = ((total_comparisons - mismatches) / total_comparisons) * 100
        logger.info(f"Overall Best Bid/Ask Acc  : {accuracy:.2f}%")
        
        if accuracy == 100.0:
            logger.info("SUCCESS: The deterministic L3 order book tracks Bitfinex snapshots completely.")
            sys.exit(0)
        else:
            logger.error("FAILED: Discrepancies found between reconstructed book and actual snapshots.")
            sys.exit(1)
    else:
        logger.warning("No snapshots were evaluated. Check timings.")
        sys.exit(0)


if __name__ == "__main__":
    main()
