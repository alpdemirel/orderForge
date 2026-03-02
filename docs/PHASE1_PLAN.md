# Phase 1 Plan (Weeks 1-3)

## Week 1 - Monorepo and capture skeleton
Deliverables:
- Python-first monorepo scaffold with module boundaries.
- Python CI pipeline (install, test on Ubuntu).
- Capture stub module that writes required line format:
  `<local_ts_ns>|<channel>|<raw_json>`.
- Placeholder tests running in CI.
- Baseline architecture and setup docs.

Outcome:
- Team can clone, build, run tests, and start parallel feature work safely.

## Week 2 - Real capture pipeline (simple to moderate)
Deliverables:
- Websocket listener (single connection first).
- In-memory queue and dedicated writer worker.
- Incremental and snapshot file separation.
- Snapshot schedule every 10 minutes.
- UTC midnight file rotation.

Outcome:
- Reliable single-source daily capture flow with basic operational correctness.

## Week 3 - Replay + simulator skeleton (moderate to complex)
Deliverables:
- `market_data` deterministic replay reader for captured files.
- Normalized event interface in `common`.
- `exchange` initial order book + basic matching constraints scaffold.
- End-to-end smoke path: replay file -> exchange simulator -> test assertion.
- Profiling pass to identify whether any hot path should be moved to C++.

Outcome:
- First deterministic simulation loop ready for strategy integration.

## Guiding progression
- Start with minimal interfaces and runnable targets.
- Add one correctness guarantee at a time (format -> ordering -> determinism).
- Keep components loosely coupled so capture, replay, and exchange can evolve independently.
- Only migrate to C++ after profiling proves Python is a bottleneck.
