# OrderForge

OrderForge is a deterministic market simulation pipeline for backtesting trading strategies on BTC-USD using Bitfinex L3 (individual order) market data.

## Architecture

```
Bitfinex L3 feed -> capture (EC2) -> daily files + S3
                                          |
                                          v
                            market_data/reader.py (parse)
                                          |
                                          v
                            market_data/replayer.py (replay loop)
                                          |
                    +-----------+---------+---------+
                    |           |                   |
                    v           v                   v
              exchange/    sim/latency_queue    strategy/
              (LOB +       (delayed order       (Avellaneda-Stoikov
               matching)    landing via heap)    market making)
```

## Repository layout

- `capture/` - Live Bitfinex L3 WebSocket capture (EC2 colocated, dual-timestamp)
- `market_data/` - File parser and deterministic replay engine
- `exchange/` - Order book, matching engine, market event applicator, strategy gateway
- `strategy/` - Base strategy ABC and Avellaneda-Stoikov implementation
- `sim/` - Simulation orchestrator, latency queue, notifying exchange adapter
- `common/` - Shared types (`MarketEvent`) and time utilities
- `tests/` - pytest test suite
- `docs/` - Architecture, setup, and operational guides
- `scripts/` - Diagnostic, analysis, and backtest helper scripts

## Data format

Every captured line uses the dual-timestamp format (EC2 colocated with exchange):

```
<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
```

- `local_ts_ns` - When we received the message (EC2 wall clock, nanoseconds)
- `exchange_ts_ns` - When the exchange created the event (Bitfinex TIMESTAMP flag, nanoseconds)
- `channel` - `bf_snapshot`, `bf_update`, `bf_info`, `bf_subscribed`
- `raw_json` - Original Bitfinex payload (mts stripped)

Legacy 3-field format (`<local_ts_ns>|<channel>|<raw_json>`) is supported for backwards compatibility; `exchange_ts_ns` defaults to 0.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Run tests

```bash
pytest
```

## Run capture (EC2 production)

```bash
export S3_BUCKET="orderforge-s3-bucket"
python -m capture.main ./data
```

Writes to `data/YYYY-MM-DD/events.log`, rotates at UTC midnight, uploads to S3.

## Run backtest

```bash
# Quick summary
python -m sim.main data/2026-04-15/events.log --risk-aversion 0.001 --order-size 0.01

# Detailed analysis report
python scripts/run_backtest_analysis.py data/2026-04-15/events.log
```

## Key design decisions

- **Fixed-point arithmetic** - Price x10 (1 tick = $0.1), quantity x100M (1 satoshi). No floating-point drift.
- **Dual timestamps** - Real exchange timestamps via Bitfinex TIMESTAMP conf flag (32768). Per-tick latency: `local_ts - exchange_ts`.
- **Latency emulation** - Strategy orders land on the exchange after a predicted delay: `landing_ts = local_ts + latency_estimate`. Orders held in a min-heap, drained each tick.
- **Queue position modeling** - Market-order depth ahead of strategy orders is subtracted before filling. Prevents optimistic fills.
- **Deterministic replay** - Same input file always produces the same fills, P&L, and inventory trajectory. No RNG in the matching engine.

## Documentation

- `docs/PROJECT_GUIDE.md` - Team walkthrough and execution plan
- `docs/WORK_PARTITION.md` - 4-person role responsibilities
- `docs/DATA_RETRIEVAL.md` - How to pull captured data from S3 or EC2
- `docs/BITFINEX_SETUP_GUIDE.md` - EC2 + S3 deployment guide
- `docs/NORMALIZED_EVENT_MAPPING.md` - L3 event to order book mapping contract
- `docs/DEV_SETUP.md` - Local development environment setup
- `docs/EC2_DAILY_LOG_ARCHIVAL.md` - Daily log compression and S3 archival
