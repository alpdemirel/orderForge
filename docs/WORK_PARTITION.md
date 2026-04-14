# OrderForge Work Partition (4-Person Detailed Plan)

This document defines how 4 team members divide work, why each workstream exists,
and the step-by-step sequence to execute with minimal integration risk.

---

## 1) Team structure summary

We split the project into 4 roles:

1. **Capture Engineer** (EC2 + Bitfinex L3 WebSocket + file writing)
2. **Market Data Engineer** (file parsing + deterministic replay + normalization)
3. **Exchange Engineer** (order book + matching + account/fees)
4. **Integration & Strategy Engineer** (strategy gateway, sim pipeline, tests, CI)

Why this split:
- It matches actual module boundaries.
- It reduces overlap and merge conflicts.
- It allows parallel development after interface definitions are locked.

---

## 2) Global rules (all teammates must follow)

1. **Do not break shared contracts in `common/` without team agreement.**
2. **No PR without tests.**
3. **If behavior changes, update docs.**
4. **Keep modules independent; communicate via defined interfaces only.**
5. **Preserve capture line format exactly:**
   - `<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>`
6. **Treat capture as L3-first (Bitfinex R0 Raw Books) unless team-approved change.**
7. **Keep snapshot cadence at 10 minutes and rotate at UTC midnight.**

---

## 3) Person A - Capture Engineer

### Mission
Your job is to be the project's "camera operator."
You continuously record what the real market says, without changing it, so the rest of the team can
replay the same day later and test strategies fairly.
The recording target is Bitfinex L3 (R0 Raw Books) market data.

### What you own
- Main code area: `capture/`
- Capture tests: `tests/` (format, rotation, snapshot interval, reconnect behavior)
- Cloud runtime: EC2 for live process, S3 for durable storage

### Tools
- Python (`capture/main.py`)
- Bitfinex R0 Raw Books WebSocket feed (`wss://api-pub.bitfinex.com/ws/2`)
- Async queue for listener/writer separation
- EC2 (run 24/7 capture), S3 (store rotated files)
- `pytest`

### Implementation sequence

#### Step A1 - Writer with dual-timestamp format [DONE]
Built `build_line()` that produces lines in the format:
```
<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
```
Tests validate line format correctness.

#### Step A2 - Bitfinex R0 WebSocket listener [DONE]
Connected to `wss://api-pub.bitfinex.com/ws/2` with:
- `prec: "R0"` for individual order IDs (true L3)
- `freq: "F0"` for real-time updates
- `symbol: "tBTCUSD"`
- `len: "100"` (100 orders per side in snapshots)
- Conf flags: `163840` = `131072` (OB_CHECKSUM) + `32768` (TIMESTAMP)

The TIMESTAMP flag (32768) causes Bitfinex to append a millisecond timestamp (`mts`) to every
array message. `classify_bitfinex_msg()` extracts and strips it, converting to nanoseconds for
the `exchange_ts_ns` field.

#### Step A3 - Listener/writer separation with async queue [DONE]
Listener puts `(channel, raw_json, exchange_ts_ns)` tuples into an `asyncio.Queue(maxsize=50000)`.
Writer reads from queue and writes to disk. Non-blocking flow.

#### Step A4 - File rotation and snapshots [DONE]
- Incremental L3 event logs: `data/YYYY-MM-DD/events.log`
- 10-minute snapshots: `data/YYYY-MM-DD/snapshot_HHMMSS.log`
- UTC midnight rotation with automatic day-change detection

#### Step A5 - Reliability [DONE]
- Reconnect with exponential backoff (5s initial, 60s cap)
- Heartbeat/ping monitoring via websockets library
- In-memory book state maintained for snapshot accuracy

#### Step A6 - Cloud deployment and S3 [DONE]
- Running on EC2 instance `i-0cdb00a6fca91abdd` (3.121.130.22)
- IAM role for S3 access (no hardcoded keys)
- S3 upload for snapshots (every 10 min) and daily archives (at midnight)
- Daily tar.gz compression and old directory cleanup

#### Step A7 - Exchange timestamps [DONE]
Enabled the Bitfinex TIMESTAMP conf flag (32768) so every message carries a real exchange
timestamp. Measured latency: ~5.6ms EC2-to-Bitfinex. Old data (before April 13, 2026) has
`exchange_ts_ns=0`; the sim layer handles this via `default_latency_ms` fallback.

### Deliverables checklist
- [x] Live capture process running on EC2
- [x] Correct dual-timestamp line format in all output files
- [x] Separate incremental L3 logs and snapshot files
- [x] Snapshot every 10 minutes
- [x] UTC midnight rotation with compression and archival
- [x] Files uploaded to S3
- [x] Real exchange timestamps via TIMESTAMP flag
- [x] Tests for format/rotation/snapshot/reconnect

---

## 4) Person B - Market Data Engineer

### Mission
Your job is to be the project's "replay director."
Person A records the market raw footage; you make sure we can play that footage back in the exact
right order, every time, for fair simulation.

### What you own
- Main code area: `market_data/`
- Shared replay/event contracts in `common/`
- Replay tests in `tests/`

### Implementation sequence

#### Step B1 - File readers [DONE]
Built `reader.py` with:
- `parse_line()`: Handles both 4-field (current) and 3-field (legacy) formats
- `read_capture_file()`: Streaming iterator, yields `MarketEvent` objects
- Strict vs permissive mode for malformed lines
- Helper functions: `extract_bitfinex_snapshot_orders()`, `extract_bitfinex_update()`

#### Step B2 - Deterministic ordering [DONE]
Primary ordering: file order (which preserves `local_ts_ns` order from capture).
Bitfinex R0 does not provide sequence numbers. Events are processed in capture-arrival order.

#### Step B3 - Normalized events [DONE]
`MarketEvent` dataclass in `common/types.py`:
- `local_ts_ns`, `exchange_ts_ns`, `channel`, `raw_json`
- Clean separation between parsing and consumption

#### Step B4 - Replay engine [DONE]
`Replayer` class in `market_data/replayer.py`:
- Clock synchronization (ns -> ms, dual timestamp handling)
- Latency queue drain on each tick
- Market event application to exchange engine
- Top-of-book + depth emission to strategy
- Supports both real exchange timestamps and synthetic latency fallback

### Deliverables checklist
- [x] Reader for incremental and snapshot files (both formats)
- [x] Deterministic ordering (file order = capture order)
- [x] Normalized `MarketEvent` interface in `common/`
- [x] Replay engine with clock sync and latency support
- [x] Tests for parsing, determinism, and boundary cases

---

## 5) Person C - Exchange Engineer

### Mission
Your job is to be the project's "market referee."
Given replayed market events and strategy orders, you decide what actually gets filled, rejected, or
left pending according to exchange rules.

### What you own
- Main code area: `exchange/`
- Exchange behavior tests in `tests/`

### Implementation sequence

#### Step C1 - Core domain objects [DONE]
`models.py`:
- `Order`, `Fill`, `ExecutionResult`, `AccountState`
- `Side` (BUY/SELL), `TimeInForce` (GTC/IOC/POST_ONLY), `OrderStatus`
- Fixed-point: `PRICE_MULTIPLIER=10`, `QUANTITY_MULTIPLIER=100_000_000`
- `MARKET_OWNER_ID="__market__"` for passive book orders

#### Step C2 - Order book [DONE]
`order_book.py`:
- Price-time priority with sorted price level indices (`bisect`)
- `deque` queues at each level for FIFO ordering
- O(1) `best_bid()`/`best_ask()`, O(log n) `add()`/`cancel()`
- `depth_snapshot()`, `market_depth_ahead()` for queue position
- `pop_best_filtered()` for strategy-order matching
- `worst_orders()` for R0 window pruning
- Monotonic `book_seq` for FIFO ordering

#### Step C3 - Matching engine [DONE]
`engine.py`:
- `submit_order()`: Standard price-time priority matching for strategy orders
- `match_incoming_against_strategy()`: Market orders vs resting strategy orders with queue position modeling
- Maker fee 10 BPS, taker fee 20 BPS (Bitfinex rates)
- GTC, IOC, POST_ONLY support
- Full account state tracking (cash, position, fees)

#### Step C4 - Market event applicator [DONE]
`market_event_applicator.py`:
- L3 -> LOB bridge: translates `bf_snapshot`/`bf_update` into book operations
- Snapshot: clears market orders, re-inserts from snapshot (strategy orders survive)
- Update: cancel-replace semantics, crossing detection against strategy orders
- R0 window pruning (100 orders/side max)
- Bitfinex ID to internal ID mapping

#### Step C5 - Strategy gateway [DONE]
`gateway.py`:
- Thin wrapper: `submit_order()`, `cancel_order()`, `get_order()`, `get_account_state()`
- Isolates strategy code from engine internals

### Deliverables checklist
- [x] Order and fill domain objects with fixed-point arithmetic
- [x] Deterministic order book with price-time priority
- [x] Matching engine with GTC/IOC/Post-only
- [x] Market event applicator with queue position modeling
- [x] Strategy gateway API
- [x] Edge-case and determinism tests

---

## 6) Person D - Strategy & Integration Engineer

### Mission
Your job is to be the project's "orchestrator and quality gatekeeper."
You connect all modules into one working pipeline and build the strategy logic.

### What you own
- Main code area: `strategy/`, `sim/`
- Cross-module integration tests in `tests/`
- CI reliability and workflow consistency

### Implementation sequence

#### Step D1 - Strategy interface [DONE]
`strategy/base.py`:
- `BaseStrategy` ABC with `on_market_data()` and `on_order_fill()` callbacks
- `update_clock()`: Sets internal clock and computes `latency_estimate = local_ts - exch_ts`
- `submit_order()`: Converts float price/amount to fixed-point, routes through latency queue
- `cancel_order()`: Local cancel for in-flight, delayed cancel for resting orders
- `strategy/noop.py`: No-op baseline for testing

#### Step D2 - Avellaneda-Stoikov strategy [DONE]
`strategy/avellaneda_stoikov.py`:
- Reservation price: `r = s - q * gamma * sigma^2 * (T-t)`
- Optimal half-spread: `delta = (gamma * sigma^2 * (T-t)) / 2 + (1/gamma) * ln(1 + gamma/k)`
- Time-weighted realized volatility estimator (rolling window of 100 samples)
- Cancel-replace quoting cycle on each market data tick
- No look-ahead bias (quotes computed from prior-tick volatility)

#### Step D3 - Latency queue [DONE]
`sim/latency_queue.py`:
- Min-heap of `(landing_ts_ms, seq, action, payload)` entries
- `enqueue_submit()`, `enqueue_cancel()`, `drain()`, `cancel_inflight()`
- Landing time formula: `landing_ts = current_local_ts + current_latency_estimate`

#### Step D4 - Notifying exchange [DONE]
`sim/notifying_exchange.py`:
- Adapter between latency queue and gateway
- Routes fills from both paths (strategy-submitted and market-crossed)
- Records all fills in `emitted_fills` for post-backtest analysis

#### Step D5 - End-to-end pipeline [DONE]
`sim/main.py`:
- `build_pipeline()`: Wires strategy -> notifying exchange -> gateway -> engine + latency queue
- `run()`: Full replay with P&L summary
- CLI with `--risk-aversion`, `--order-size`, `--session-duration`, `--default-latency-ms`

#### Step D6 - Analysis scripts [DONE]
- `scripts/run_backtest_analysis.py`: Detailed P&L, VWAP, drawdown, efficiency report
- `scripts/validate_determinism.py`: Proves same input -> same output
- `scripts/validate_exchange_timestamps.py`: Verifies real timestamps in captured data

### Deliverables checklist
- [x] Strategy interface and baseline no-op strategy
- [x] Avellaneda-Stoikov market making implementation
- [x] Latency queue with min-heap
- [x] Notifying exchange fill routing
- [x] End-to-end pipeline with CLI
- [x] Integration and determinism tests

---

## 7) Handoff contracts between people

These are the key dependencies:

- **A -> B**: Dual-timestamp capture file format, naming conventions, and L3 channel/message mapping.
- **B -> C**: Normalized `MarketEvent` stream via `Replayer`.
- **C -> D**: `ExchangeGateway` API and `Fill` events.
- **D -> Team**: Integration feedback, contract mismatch reports, CI health.

Rule:
- No one silently changes handoff formats.
- Changes require a short design note and team approval.

---

## 8) Acceptance checklist per person

A task is accepted only if:
- Code works locally.
- Tests cover expected behavior.
- CI passes.
- Docs reflect final behavior.
- Another teammate can run it without guessing.
