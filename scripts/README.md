# Scripts

Helper and diagnostic scripts for OrderForge.

## Backtest & Analysis

| Script | Purpose |
|--------|---------|
| `run_backtest_analysis.py` | Full backtest with detailed P&L report (fills, VWAP, drawdown, efficiency) |
| `run_sim.py` | Quick simulation runner (proof-of-concept harness) |
| `run_accuracy_backtest.py` | Backtest focused on fill accuracy metrics |

## Diagnostics

| Script | Purpose |
|--------|---------|
| `diagnose_accuracy.py` | Investigate accuracy issues in simulation results |
| `diagnose_fill_accuracy.py` | Detailed fill-level accuracy analysis |
| `gap_analysis.py` | Detect gaps or discontinuities in captured data |
| `health_check.py` | Verify capture process health and data integrity |

## Validation

| Script | Purpose |
|--------|---------|
| `validate_determinism.py` | Prove deterministic replay (same input -> same output) |
| `validate_exchange_timestamps.py` | Verify real exchange timestamps in captured data, report latency distribution |
| `verify_alignment.py` | Check alignment between different data sources |
| `verify_l3_and_s3.py` | Verify L3 data format and S3 upload correctness |
| `compare_snapshots.py` | Compare order book snapshots for consistency |
| `test_bitfinex_time_flag.py` | Live test of Bitfinex TIMESTAMP conf flag (32768) |
