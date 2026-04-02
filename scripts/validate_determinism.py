#!/usr/bin/env python3
"""
Diagnostic tool to validate perfectly reproducible, deterministic simulation replay.

Runs the trading simulation multiple times on the same input file(s) and ensures
the final summary (PnL, position, filled orders, resting books) matches exactly
byte-for-byte across all runs.
"""

import sys
import json
import logging
import argparse
from pathlib import Path

# Ensure import of project modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.main import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def validate_file(filepath: Path, runs: int = 2) -> bool:
    """Run simulation `runs` times on `filepath` and confirm identical output."""
    logger.info(f"Validating determinism on: {filepath.name} (Running {runs} times)")
    
    if not filepath.exists():
        logger.error(f"File not found: {filepath}")
        return False
        
    outputs = []
    
    for i in range(runs):
        logger.info(f"  [Run {i+1}/{runs}] Starting simulation...")
        
        try:
            # Inject silent logger or mute it to avoid explosive terminal output during checks if needed.
            # But the primary run() handles its own logging cleanly.
            summary = run(str(filepath), risk_aversion=0.1, order_size=0.01)
            
            # Convert dictionary to strict JSON string to guarantee structure/type hashability
            json_representation = json.dumps(summary, sort_keys=True)
            outputs.append(json_representation)
            
            logger.info(f"  [Run {i+1}/{runs}] Completed. (Fills: {summary['fills']})")
        except Exception as e:
            logger.error(f"  [Run {i+1}/{runs}] Crashed during execution: {e}")
            return False

    # Check that all outputs are identical
    baseline = outputs[0]
    for i, out in enumerate(outputs[1:], start=1):
        if out != baseline:
            logger.error(f"❌ NON-DETERMINISM DETECTED on {filepath.name}!")
            logger.error(f"Difference occurred between Run 1 and Run {i+1}.")
            # A simple mismatch indicator (diff logic could be added)
            return False
            
    logger.info(f"✅ EXACT MATCH. Simulation on {filepath.name} is fully deterministic.\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Deterministic Replay Validator")
    parser.add_argument("capture_files", nargs="+", help="Paths to capture log files (e.g., events.log)")
    parser.add_argument("--runs", type=int, default=2, help="Number of identical runs to perform per file (default: 2)")
    
    args = parser.parse_args()
    
    all_passed = True
    for f_path in args.capture_files:
        path_obj = Path(f_path)
        passed = validate_file(path_obj, runs=args.runs)
        if not passed:
            all_passed = False
            
    if all_passed:
        logger.info("🎉 SUCCESS: All provided files proved perfectly reproducible outputs.")
        sys.exit(0)
    else:
        logger.error("💥 FAIL: Discrepancies detected in replay output. Check logic for non-determinism (e.g. dict ordering, unsorted logic, system clocks).")
        sys.exit(1)

if __name__ == "__main__":
    main()
