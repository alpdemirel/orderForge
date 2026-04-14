# OrderForge Project Guide (Team Walkthrough)

This document explains the project in a practical, step-by-step way for team members who know the
high-level idea but need the implementation details and execution plan.

---

## 1) What are we building?

OrderForge is a high-fidelity backtest simulator for Bitfinex BTC-USD L3 market data.

In simple terms:
1. We **capture** real exchange messages (individual orders, not aggregated levels).
2. We **replay** them deterministically through a simulated exchange.
3. The exchange **matches** strategy orders against real historical liquidity.
4. We plug in a **strategy** (Avellaneda-Stoikov market maker) and evaluate results.

The goal is to make this pipeline realistic enough that strategy behavior in simulation is close to
real market behavior, including realistic latency modeling.

---

## 2) Core project idea in one pipeline

The full data flow:

```
Bitfinex L3 feed -> capture (EC2) -> daily files + S3 -> deterministic replay -> exchange simulator -> strategy
```

Each stage has one clear responsibility so we can test and debug independently.

---

## 3) Repository map (what each folder is for)

- `capture/`
  - Live Bitfinex L3 WebSocket capture running 24/7 on EC2.
  - Writes dual-timestamp lines, rotates at UTC midnight, uploads to S3.
- `market_data/`
  - Parses capture files into `MarketEvent` objects and drives the replay loop.
- `exchange/`
  - Order book (price-time priority LOB), matching engine, market event applicator
    (L3 -> LOB bridge), and strategy-facing gateway.
- `strategy/`
  - Base strategy ABC and Avellaneda-Stoikov market making implementation.
- `sim/`
  - Simulation orchestrator (`sim/main.py`), latency queue (min-heap for delayed
    order landing), and notifying exchange adapter (fill routing).
- `common/`
  - Shared `MarketEvent` dataclass, time utilities.
- `tests/`
  - Unit, integration, and end-to-end tests.
- `docs/`
  - Architecture, setup, and operational guides.
- `scripts/`
  - Diagnostic, analysis, and backtest helper scripts.

Why this structure matters:
- Reduces merge conflicts.
- Makes ownership clear.
- Lets multiple people build in parallel.

---

## 4) Data contract

Every captured line uses the dual-timestamp format:

```
<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
```

Meaning:
- `local_ts_ns`: EC2 wall-clock timestamp in nanoseconds when message was received.
- `exchange_ts_ns`: Exchange-side timestamp in nanoseconds when the event was created
  (obtained via Bitfinex TIMESTAMP conf flag 32768). 0 for legacy data.
- `channel`: Feed channel label (`bf_snapshot`, `bf_update`, `bf_info`, etc.).
- `raw_json`: Original exchange payload (mts timestamp stripped by capture).

Legacy 3-field format (`<local_ts_ns>|<channel>|<raw_json>`) is supported for backwards
compatibility; `exchange_ts_ns` defaults to 0.

This is critical because we need:
- Reproducibility
- Traceability
- Accurate latency estimation during replay

---

## 5) File behavior

These are hard requirements:
- Keep **incremental** updates and **snapshots** in separate files.
- Emit snapshots every **10 minutes**.
- Rotate files at **UTC midnight**.
- Compress and archive previous day to S3 at rotation.
- Clean up local directories older than 1 day.
- S3 upload requires IAM role on EC2 with `s3:PutObject`.

---

## 6) Technical choices

We are Python-first.

Why:
- Team can move faster now.
- Faster learning/onboarding.
- More time on correctness and architecture early.

Key design decisions:
- **Fixed-point arithmetic**: Price x10 ($0.1 tick), quantity x100M (1 satoshi). Eliminates float drift.
- **Dual timestamps**: Real exchange timestamps via Bitfinex TIMESTAMP flag. Per-tick latency.
- **Deterministic replay**: Same input always produces same output. No RNG.
- **Latency emulation**: Orders land after predicted delay via min-heap. Queue position modeling.

Future path:
- Keep interfaces language-agnostic.
- If profiling shows bottlenecks, migrate only hot paths to C++ (not everything).

---

## 7) What has been built

### Capture (`capture/main.py`)
- Live Bitfinex R0 Raw Books WebSocket connection
- Dual-timestamp line format with real exchange timestamps (TIMESTAMP flag 32768)
- In-memory book maintenance for 10-minute snapshots
- UTC midnight rotation with tar.gz compression and S3 upload
- Reconnect with exponential backoff
- Running 24/7 on EC2 (`i-0cdb00a6fca91abdd` at `3.121.130.22`)

### Market Data (`market_data/`)
- `reader.py`: Parses both 4-field and legacy 3-field formats
- `replayer.py`: Deterministic replay loop with clock sync, latency queue drain, book updates, strategy callbacks

### Exchange (`exchange/`)
- `models.py`: Fixed-point Order, Fill, AccountState, Side, TimeInForce
- `order_book.py`: Price-time priority LOB with O(1) best bid/ask, O(log n) add/cancel, depth snapshots, queue position modeling
- `engine.py`: Matching engine with GTC/IOC/POST_ONLY, maker/taker fees (10/20 BPS), strategy-vs-market matching
- `market_event_applicator.py`: L3 -> LOB bridge, R0 window pruning (100 orders/side), snapshot clearing with strategy order preservation
- `gateway.py`: Thin wrapper for strategy-facing API

### Strategy (`strategy/`)
- `base.py`: ABC with clock sync, latency estimation, order routing through latency queue
- `avellaneda_stoikov.py`: Full AS model — reservation price, optimal spread, time-weighted volatility, cancel-replace quoting
- `noop.py`: No-op baseline for testing

### Simulation (`sim/`)
- `main.py`: End-to-end pipeline orchestrator with CLI
- `latency_queue.py`: Min-heap for delayed order/cancel landing
- `notifying_exchange.py`: Fill routing adapter (strategy-submitted + market-crossed fills)

### Scripts
- `run_backtest_analysis.py`: Detailed P&L report (fills, VWAP, drawdown, efficiency)
- `run_sim.py`: Quick simulation runner
- `validate_determinism.py`: Proves deterministic replay
- `validate_exchange_timestamps.py`: Verifies real exchange timestamps in captured data
- Diagnostic scripts for accuracy, fill analysis, gap analysis, health checks

---

## 8) How the simulation works (step by step)

For each `MarketEvent` from the capture file:

1. **Clock sync** — Convert ns timestamps to ms. Compute `exch_ts_ms` from real exchange timestamp (or synthesize from `default_latency_ms` for old data).

2. **Strategy clock update** — `strategy.update_clock(exch_ts_ms, local_ts_ms)` sets the strategy's internal clock and computes `latency_estimate = local_ts - exch_ts`.

3. **Drain latency queue** — `latency_queue.drain(exch_ts_ms)` pops and executes all strategy orders whose `landing_ts_ms <= exch_ts_ms`.

4. **Apply market event to book** — `engine.apply_market_event(event)` updates the LOB. If incoming market orders cross resting strategy orders, fills are generated.

5. **Emit market data to strategy** — Best bid/ask, mid price, and 5-level depth from the engine's book are sent to `strategy.on_market_data()`.

6. **Strategy computes quotes** — AS model calculates reservation price and optimal spread, cancels old orders, submits new bid/ask through the latency queue.

7. **Orders land later** — On a future tick when `exch_ts >= landing_ts`, the order is submitted to the exchange. If it crosses market liquidity, it fills immediately; otherwise it rests on the book.

---

## 9) Team ownership

- **Person A (Capture Engineer)**: `capture/` + EC2/S3 operations
- **Person B (Market Data Engineer)**: `market_data/` replay parser and ordering
- **Person C (Exchange Engineer)**: `exchange/` order book, matching, L3 bridge
- **Person D (Integration & Strategy Engineer)**: `strategy/`, `sim/`, cross-module tests, CI

All contributors:
- Keep contracts in `common/`
- Add tests with every behavior change
- Keep docs updated when decisions change

---

## 10) Daily developer workflow

1. Pull latest changes.
2. Activate venv.
3. Run tests.
4. Implement one small unit of work.
5. Add/update tests.
6. Re-run tests.
7. Open PR with clear summary and test proof.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest -q
```

---

## 11) Running a backtest

```bash
# Download data from S3
aws s3 cp s3://orderforge-s3-bucket/bitfinex/btcusd/2026-04-15/events.log data/2026-04-15/events.log

# Quick run
python -m sim.main data/2026-04-15/events.log --risk-aversion 0.001 --order-size 0.01

# Detailed report
python scripts/run_backtest_analysis.py data/2026-04-15/events.log
```

No `--default-latency-ms` needed for data captured after April 13, 2026 — real exchange timestamps are present.

---

## 12) Definition of done (per task)

A task is done only when:
- Behavior is implemented.
- Tests cover expected behavior.
- CI passes.
- Docs are updated if the contract/design changed.
- Another team member can run and understand it.

---

## 13) Common mistakes to avoid

- Mixing responsibilities between modules.
- Changing data formats without updating docs/tests.
- Building large features before validating small contracts.
- Skipping CI/test checks before merging.
- Prematurely rewriting everything in C++ before profiling.
- Using synthetic latency when real exchange timestamps are available.

---

## 14) How to explain this project (short pitch)

"OrderForge is a deterministic market simulation pipeline. We capture raw L3 exchange data with
dual timestamps (exchange and local), replay it through a simulated order book with realistic
latency modeling, and run an Avellaneda-Stoikov market-making strategy on top. Every component
is tested, deterministic, and modular."
