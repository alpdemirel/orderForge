# OrderForge Architecture

## Purpose
OrderForge is a deterministic exchange backtest simulator for Coinbase BTC-USD order flow.
This repository is organized as a Python-first monorepo with clear module boundaries and explicit
data contracts between stages. The architecture is language-agnostic so performance-critical parts
can move to C++ later without changing data contracts.

## Module boundaries
- `capture/`: Ingests exchange stream data and persists raw events with local receipt timestamp.
- `market_data/`: Replays captured files in exchange-time order and emits normalized events.
- `exchange/`: Simulated exchange core (order book, matching engine, fees, and account state).
- `strategy/`: Strategy implementations (Avellaneda-Stoikov in a later phase).
- `common/`: Shared types, utilities, and event model used by all modules.
- `tests/`: Cross-module test targets and integration checks.

## Data contracts
### Raw capture line format (hard requirement)
Each persisted line must follow:

`<local_ts_ns>|<channel>|<raw_json>`

Where:
- `local_ts_ns` is local wall-clock nanoseconds from Unix epoch at ingest time.
- `channel` is the source stream label (for example `level2`, `heartbeat`).
- `raw_json` is the original message payload with no semantic rewriting.

### File model (hard requirement)
- Incremental updates and snapshots are persisted into separate files.
- Snapshots are emitted every 10 minutes.
- Files rotate at UTC midnight.
- Future hardening: multi-websocket arbitration, gap detection, and S3 upload.

## Capture threading model (Week 1 design)
Capture is split into two roles:
- Listener thread: receives websocket frames and tags each message with `local_ts_ns`.
- Writer thread: reads from an in-memory queue and appends to output files.

Benefits:
- Prevents blocking network reads on slow disk I/O.
- Keeps write ordering explicit and testable.
- Allows later backpressure handling and metrics without changing interfaces.

Week 1 implementation uses a single-process Python stub script with no real websocket yet, but
keeps the output format and module boundaries aligned with this model.

## Technology choice (current phase)
- Primary language: Python for fast team iteration and easier onboarding.
- Testing: `pytest`.
- CI: GitHub Actions on Ubuntu.
- Future option: migrate hot paths (for example matching engine or replay parser) to C++ once
  correctness is stable and profiling identifies bottlenecks.
