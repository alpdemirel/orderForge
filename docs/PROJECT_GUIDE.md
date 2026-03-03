# OrderForge Project Guide (Team Walkthrough)

This document explains the project in a practical and a step-by-step way as well as the overall execution plan.

---

## 1) What are we building?

OrderForge is a high-fidelity backtest simulator for Coinbase BTC-USD market data, with
an L3-first capture and replay design.

In simple terms:
1. We **capture** real exchange messages.
2. We **replay** them deterministically.
3. We run an **exchange simulator** on top of those events.
4. We plug in a **strategy** and evaluate results.

The goal is to make this pipeline realistic enough that strategy behavior in simulation is close to
real market behavior.

---

## 2) Core project idea in one pipeline

The full data flow we are building is:

`Coinbase L3 feed -> capture -> daily files -> deterministic replay -> exchange simulator -> strategy`

Each stage has one clear responsibility so we can test and debug independently.

---

## 3) Repository map (what each folder is for)

- `capture/`
  - Ingests stream data and writes raw files.
  - Week 1 currently has a stub that writes sample lines.
- `market_data/`
  - Reads capture files and emits ordered normalized events.
- `exchange/`
  - Simulated order book, matching engine, account/fees, gateway for strategies.
- `strategy/`
  - Strategy logic (Avellaneda-Stoikov later).
- `common/`
  - Shared event/data types, utilities, and cross-module contracts.
- `tests/`
  - Unit/smoke/integration tests.
- `docs/`
  - Architecture, setup, roadmap, and this guide.
- `scripts/`
  - One-off helper scripts.

Why this structure matters:
- Reduces merge conflicts.
- Makes ownership clear.
- Lets multiple people build in parallel.

---

## 4) Non-negotiable data contract (important)

Every captured line must be:

`<local_ts_ns>|<channel>|<raw_json>`

Meaning:
- `local_ts_ns`: local machine timestamp in nanoseconds when message was received.
- `channel`: feed channel label (for example `full`, `heartbeat`, `level3`-style channels).
- `raw_json`: original exchange payload string.

This is critical because we need:
- Reproducibility
- Traceability
- Accurate replay debugging

---

## 5) Required file behavior (for upcoming weeks)

These are hard requirements we committed to:
- Keep **incremental L3 event logs** and **periodic internal snapshots** in separate files.
- Emit snapshots every **10 minutes**.
- Rotate files at **UTC midnight**.
- Later add:
  - multiple websocket arbitration
  - gap detection
  - S3 upload

Even if Week 1 does not implement all of this, design decisions now must not block these features.

---

## 6) Current technical choice and why

We are Python-first right now.

Why:
- Team can move faster now.
- Faster learning/onboarding.
- More time on correctness and architecture early.

Future path:
- Keep interfaces language-agnostic.
- If profiling shows bottlenecks, migrate only hot paths to C++ (not everything).

---

## 7) What Week 1 already delivers

Week 1 objective is scaffolding and team readiness.

Done:
- Python monorepo structure with module boundaries.
- CI on GitHub Actions (Ubuntu + install + test).
- Capture stub that writes correct line format.
- Basic tests (`pytest`) running.
- Core docs (`ARCHITECTURE`, `DEV_SETUP`, `PHASE1_PLAN`).

What Week 1 is **not**:
- No real websocket production ingestion yet.
- No full matching engine yet.
- No strategy model implementation yet.

---

## 8) Step-by-step execution plan (Weeks 1-3)

## Week 1: Foundation

Step 1: Team setup  
- Everyone clones repo, creates venv, runs tests.

Step 2: Verify shared understanding  
- Read `ARCHITECTURE.md` and this guide.

Step 3: Confirm baseline commands  
- `pytest` and `python -m capture.main` both work for everyone.

Success criteria:
- Everyone can run project.
- CI passes.
- Folder ownership is clear.

## Week 2: Real capture pipeline

Step 1: Listener component  
- Receive websocket frames from one L3 connection.

Step 2: Queue and writer separation  
- Listener only receives/timestamps.
- Writer only persists data.

Step 3: File output rules  
- Incremental L3 event files vs periodic snapshot files.
- Snapshot every 10 minutes.
- UTC midnight rotation.

Step 4: Capture tests  
- Validate format, rotation boundary, and ordering behavior.

Success criteria:
- Daily capture is stable and reproducible.

## Week 3: Replay + simulator skeleton

Step 1: Deterministic replay  
- Read day files and output events in exchange-time order.

Step 2: Exchange skeleton  
- Basic order book + simple matching constraints scaffold.

Step 3: End-to-end smoke test  
- Replay file -> exchange -> assert output.

Step 4: Profile performance  
- Decide whether Python is enough or where C++ is needed.

Success criteria:
- First complete backtest loop exists.

---

## 9) Suggested team ownership (practical split)

Use this split to avoid overlap:
- Person A: `capture/` + L3 capture tests
- Person B: `market_data/` L3 replay parser and deterministic ordering
- Person C: `exchange/` skeleton and matching rules driven by replayed L3 events
- Person D: docs consistency + CI + cross-module tests and contract checks

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

Useful commands:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest -q
python -m capture.main
```

---

## 11) Definition of done (per task)

A task is done only when:
- Behavior is implemented.
- Tests cover expected behavior.
- CI passes.
- Docs are updated if the contract/design changed.
- Another team member can run and understand it.

---

## 12) Common mistakes to avoid

- Mixing responsibilities between modules.
- Changing data formats without updating docs/tests.
- Building large features before validating small contracts.
- Skipping CI/test checks before merging.
- Prematurely rewriting everything in C++ before profiling.

---

## 13) How to explain this project in class (short pitch)

"OrderForge is a deterministic market simulation pipeline. We capture raw exchange data with precise
timestamps, replay it in a controlled order, simulate exchange behavior, and then run strategy logic
on top. We are building it in modular stages so each part is testable, reproducible, and easy to
improve."
