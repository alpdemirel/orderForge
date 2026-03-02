# OrderForge Work Partition (4-Person Detailed Plan)

This document defines exactly how 4 team members should divide work, why each workstream exists,
and the step-by-step sequence to execute with minimal integration risk.

Use this as the team's execution playbook.

---

## 1) Team structure summary

We split the project into 4 roles:

1. **Capture Engineer** (EC2 + websocket + file writing)
2. **Market Data Engineer** (file parsing + deterministic replay + normalization)
3. **Exchange Engineer** (order book + matching + account/fees scaffold)
4. **Integration & Strategy Engineer** (strategy gateway, tests, CI, contract enforcement)

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
   - `<local_ts_ns>|<channel>|<raw_json>`
6. **Keep snapshot cadence at 10 minutes and rotate at UTC midnight.**

---

## 3) Person A - Capture Engineer

## Mission (simple)
Your job is to be the project's "camera operator."  
You continuously record what the real market says, without changing it, so the rest of the team can
replay the same day later and test strategies fairly.

## Storyline (what you are doing in plain language)
Think of a normal finance day:
- You are not making trading decisions.
- You are not simulating the exchange.
- You are only building a trustworthy "market recorder."

Daily-life analogy:
- Like keeping your bank transaction history exactly as it happened.
- If one transaction is missing or timestamped wrong, your monthly statement and budget analysis are
  wrong.
- Same here: if capture is wrong, replay and strategy results are wrong.

## Why your work is critical to the whole project
- `market_data/` needs clean files from you to replay events in order.
- `exchange/` needs accurate event flow to simulate matching correctly.
- `strategy/` needs realistic simulation inputs to evaluate performance honestly.
- If your data has gaps, all downstream results become questionable.

## What you own
- Main code area: `capture/`
- Capture tests: `tests/` (format, rotation, snapshot interval, reconnect behavior)
- Runtime scripts/config references if needed: `scripts/`
- Cloud runtime path: EC2 for live process, S3 for durable storage

## Tools you will use
- Python (`capture/main.py` and new capture modules)
- Coinbase websocket feed
- Queue/threading or async tools for listener/writer separation
- EC2 (run 24/7 capture service)
- S3 (store rotated files safely)
- `pytest` (prove behavior is correct)

## Detailed sequence (what to do, why, and output)

### Step A1 - Build a trustworthy writer first
What:
- Create writer utilities that append lines safely and consistently.
- Keep exact line contract:
  - `<local_ts_ns>|<channel>|<raw_json>`

Why:
- File format is your promise to the rest of the team.
- If format changes, parser/replay breaks.

Output:
- A stable writer function with tests for line format.

### Step A2 - Add one websocket listener (start simple)
What:
- Connect to Coinbase stream with one connection.
- For each message, capture:
  - local receive timestamp (ns)
  - channel
  - raw JSON message

Why:
- One stable connection is easier to debug than many.
- Correctness first, complexity later.

Output:
- Live message intake feeding your writer path.

### Step A3 - Separate listener and writer with a queue
What:
- Listener puts events into an in-memory queue.
- Writer reads queue and writes to disk.

Why:
- Network input and disk writes run at different speeds.
- This prevents losing messages when disk is slow.

Output:
- Non-blocking capture flow with clean component boundaries.

### Step A4 - Enforce project file rules
What:
- Store incremental updates and snapshots in separate files.
- Write snapshots every 10 minutes.
- Rotate to new files at UTC midnight.

Why:
- These are hard project requirements.
- They make replay deterministic by day and by data type.

Output:
- Daily files that are predictable for replay and grading demos.

### Step A5 - Add reliability behavior
What:
- Reconnect on disconnect (with backoff).
- Detect stalled connection (heartbeat timeout).
- Add logs for reconnect and error events.

Why:
- Real websocket feeds disconnect.
- Without recovery, you create data gaps.

Output:
- Capture service that survives normal network failures.

### Step A6 - Run in cloud and move data safely
What:
- Run capture process on EC2 continuously.
- Upload rotated files to S3.
- Confirm teammate can download same files from S3 locally.

Why:
- EC2 gives stable always-on runtime.
- S3 is durable and easy for team sharing.

Output:
- Reliable EC2 -> S3 -> local data pipeline.

### Step A7 - Document and explain each decision
What:
- Keep short notes of:
  - what you built
  - why you built it that way
  - what contract it guarantees

Why:
- You will need to explain design decisions to TAs later.

Output:
- Weekly explanation-ready log for demos and reports.

#Your workings will be explained in such way:

1. **Objective**: "I worked on market data capture reliability."
2. **Implementation**: "I added websocket listener + queue + writer separation."
3. **Reason**: "This prevents disk I/O delays from causing message loss."
4. **Evidence**: "Tests pass for format, 10-minute snapshots, and UTC rotation."
5. **Impact**: "Downstream replay/exchange modules now receive consistent daily files."

## Deliverables checklist for Person A
- [ ] Live capture process running on EC2
- [ ] Correct line format in all output files
- [ ] Separate incremental and snapshot files
- [ ] Snapshot every 10 minutes
- [ ] UTC midnight rotation
- [ ] Files uploaded to S3
- [ ] Tests for format/rotation/snapshot/reconnect
- [ ] Weekly notes ready for TA explanation

---

## 4) Person B - Market Data Engineer

## Mission (simplified)
Your job is to be the project's "movie editor and replay director."  
Person A records the market raw footage; you make sure we can play that footage back in the exact
right order, every time, for fair simulation.

## Storyline (what you are doing in plain language)
You are not collecting live data and not matching orders.  
You take stored capture files and turn them into a clean, deterministic event stream for the
exchange simulator.

Daily-life finance analogy:
- Imagine downloading your full bank history and trying to rebuild your spending day.
- If transactions are out of order or duplicated, your budget summary is wrong.
- Your role is to sort and standardize that history so anyone can replay the same day and get the
  same result.

## Why your work is critical to the whole project
- `exchange/` depends on correctly ordered events to simulate fills realistically.
- `strategy/` depends on stable replay to compare strategy performance fairly.
- If replay is inconsistent, one strategy run cannot be compared to another.

## What you own
- Main code area: `market_data/`
- Shared replay/event contracts in `common/` (with team review)
- Replay tests in `tests/`

## Tools you will use
- Python (`market_data/` modules)
- File readers/parsers for capture outputs
- Deterministic sorting/merge logic
- Event normalization utilities in `common/`
- `pytest` for replay correctness and repeatability

## Detailed sequence (what to do, why, and output)

### Step B1 - Build robust file readers
What:
- Read incremental and snapshot files by UTC day.
- Parse each line using capture contract:
  - `<local_ts_ns>|<channel>|<raw_json>`
- Handle malformed lines in a predictable way (strict or permissive mode).

Why:
- Downstream modules should not guess file structure.
- Predictable error handling prevents hidden bugs.

Output:
- Reliable input layer that turns raw lines into internal records.

### Step B2 - Define deterministic ordering rules
What:
- Decide primary ordering key(s) and tie-breakers.
- Apply same ordering logic in all runs.
- Document the rule in code and docs.

Why:
- Determinism is the core requirement for backtesting.
- Same input must always produce the same replay sequence.

Output:
- A replay sequence that is repeatable run-to-run.

### Step B3 - Normalize events for exchange consumption
What:
- Convert raw records into normalized event objects.
- Keep fields minimal and explicit (no hidden transformations).
- Add field mapping notes in `common/`.

Why:
- `exchange/` should consume a clean, stable event interface.
- Clear schema reduces integration friction.

Output:
- Stable normalized event model shared across modules.

### Step B4 - Expose replay engine API
What:
- Build replay API/iterator that emits normalized events.
- Provide basic controls:
  - start/stop
  - replay mode/speed (simple first)
  - deterministic guarantees

Why:
- Integration engineer needs a clear entry point for end-to-end testing.
- Strategy testing requires repeatable replay behavior.

Output:
- A reusable replay component callable by tests and integrations.

### Step B5 - Validate with deterministic tests
What:
- Write tests proving:
  - same input gives same output sequence
  - day-boundary behavior is correct
  - malformed line policy behaves as documented

Why:
- Replay bugs are often subtle; tests catch regressions early.

Output:
- Trustworthy replay behavior with regression protection.

### Step B6 - Keep a replay "explanation log"
What:
- Track short notes each week:
  - ordering rules used
  - normalization decisions
  - known assumptions/limits

Why:
- You need to explain technical choices clearly to TAs and teammates.

Output:
- Semester-long decision trail for demos/reports.

## Your workings will be explained in such way:

1. **Objective**: "I worked on deterministic replay and event normalization."
2. **Implementation**: "I built parsers for capture files and a stable event ordering pipeline."
3. **Reason**: "Without deterministic ordering, backtest results are not comparable."
4. **Evidence**: "Replay tests prove same input files always produce identical event sequences."
5. **Impact**: "Exchange and strategy modules now run on a consistent, reproducible market stream."

## Deliverables checklist for Person B
- [ ] Reader for incremental and snapshot files
- [ ] Deterministic ordering logic (documented)
- [ ] Normalized event interface in `common/`
- [ ] Replay API usable by integration tests
- [ ] Tests for determinism and boundary cases
- [ ] Weekly notes ready for TA explanation

---

## 5) Person C - Exchange Engineer

## Mission (simple)
Your job is to be the project's "market referee."  
Given replayed market events and strategy orders, you decide what actually gets filled, rejected, or
left pending according to exchange rules.

## Storyline (what you are doing in plain language)
You are not collecting data and not choosing strategy logic.  
You build the rule engine that answers: "If this order arrived now, what would the exchange do?"

Daily-life finance analogy:
- Think of a cashier line at a busy store.
- Customers arrive with buy/sell intentions, and the cashier follows strict service rules.
- If queue/order rules are wrong, receipts are wrong.
- Your simulator is that cashier: fair, rule-based, and deterministic.

## Why your work is critical to the whole project
- `strategy/` performance metrics depend on realistic fill outcomes.
- `market_data/` can replay perfectly, but if matching logic is wrong, results are still meaningless.
- The TA will evaluate realism through your order and fill behavior.

## What you own
- Main code area: `exchange/`
- Exchange-facing contracts in `common/` (with team review)
- Exchange behavior tests in `tests/`

## Tools you will use
- Python (`exchange/` modules)
- Data structures for order book state (price levels, queues, order maps)
- Deterministic matching logic for order handling
- Interface definitions for strategy gateway
- `pytest` for rule correctness and regression checks

## Detailed sequence (what to do, why, and output)

### Step C1 - Define core trading objects
What:
- Create clear structures for:
  - orders
  - sides (buy/sell)
  - time-in-force (GTC/IOC/Post-only)
  - fills/rejections

Why:
- Everything in matching depends on unambiguous order state.
- Clear data models reduce logic bugs later.

Output:
- Stable domain objects used across exchange logic and tests.

### Step C2 - Build deterministic order book state
What:
- Implement in-memory bid/ask representation.
- Support insert/update/cancel operations.
- Keep deterministic behavior for equal-price ordering.

Why:
- Matching is impossible without reliable book state.
- Determinism is required for repeatable backtest results.

Output:
- A predictable order book core with basic operations.

### Step C3 - Implement baseline matching rules
What:
- Add first-pass support for:
  - GTC
  - IOC
  - Post-only
- Define and document simplified behavior assumptions.

Why:
- These order constraints are core project requirements.
- Strategy testing needs these semantics early.

Output:
- Rule-based matching engine skeleton ready for iteration.

### Step C4 - Add account/fee integration hooks
What:
- Add placeholders for balances, positions, and fees.
- Keep interfaces extension-friendly.

Why:
- Full accounting may come later, but API must be ready now.
- Avoid breaking changes when adding detailed fee logic.

Output:
- Exchange outputs that can later include realistic accounting.

### Step C5 - Expose strategy gateway API
What:
- Provide clean methods to:
  - submit orders
  - cancel orders
  - query order state
- Keep API stable and documented.

Why:
- Person D needs a reliable integration point.
- A stable gateway prevents strategy code from coupling to internals.

Output:
- A clear exchange API contract for strategy integration.

### Step C6 - Validate with rule-focused tests
What:
- Test each order type and edge case:
  - empty book
  - partial fill
  - cancel flow
  - post-only rejection paths
- Add deterministic sequence tests for repeated runs.

Why:
- Matching bugs are expensive and hard to find manually.
- Tests protect core behavior during future updates.

Output:
- Verified exchange core with regression safety.

### Step C7 - Keep an "exchange decisions" log
What:
- Write brief weekly notes:
  - rule assumptions used
  - edge cases handled
  - known limitations

Why:
- You must justify realism choices to TAs and teammates.

Output:
- A clear decision trail for demos/reports.

## TA explanation template (use this all semester)
When asked "What did you do and why?", answer in this format:

1. **Objective**: "I implemented exchange matching behavior and order book logic."
2. **Implementation**: "I built deterministic order handling for GTC/IOC/Post-only and exposed a strategy gateway."
3. **Reason**: "Without realistic matching rules, strategy backtest outcomes are not credible."
4. **Evidence**: "Rule and edge-case tests pass, including deterministic repeated-run checks."
5. **Impact**: "Strategy and integration modules now receive realistic, reproducible fill behavior."

## Deliverables checklist for Person C
- [ ] Order and fill domain objects defined
- [ ] Deterministic order book scaffold implemented
- [ ] Baseline GTC/IOC/Post-only logic implemented
- [ ] Strategy gateway API documented and usable
- [ ] Edge-case and determinism tests in place
- [ ] Weekly notes ready for TA explanation

---

## 6) Person D - Integration & Strategy Engineer

## Mission (simple)
Your job is to be the project's "orchestrator and quality gatekeeper."  
You connect all modules into one working pipeline and ensure the full system stays testable,
understandable, and demo-ready.

## Storyline (what you are doing in plain language)
Others build parts of the machine; you make sure the machine runs as one system.  
You also build the strategy integration layer so the project can produce meaningful backtest runs.

Daily-life finance analogy:
- Imagine preparing a monthly personal finance report.
- One person gives bank transactions, one gives card statements, one gives cash logs.
- Your job is to merge them into one accurate report and check consistency.
- If integration is weak, numbers look fine individually but fail together.

## Why your work is critical to the whole project
- Parallel development fails without active integration ownership.
- You detect contract mismatches early before they become late-stage blockers.
- CI/test discipline from your side protects team velocity and grading confidence.

## What you own
- Main code area: `strategy/`
- Cross-module integration tests in `tests/`
- CI reliability and workflow consistency (`.github/workflows/`)
- Contract alignment checks across A/B/C outputs

## Tools you will use
- Python (`strategy/` and integration test modules)
- Replay API from `market_data/`
- Gateway API from `exchange/`
- Test harness and fixtures in `tests/`
- GitHub Actions CI + `pytest`
- Documentation updates (`docs/`)

## Detailed sequence (what to do, why, and output)

### Step D1 - Define strategy interface skeleton
What:
- Create a minimal strategy interface with callbacks such as:
  - `on_event`
  - `on_fill`
  - optional timer/init hooks
- Add one no-op baseline strategy implementation.

Why:
- Establishes consistent entry point for all future strategies.
- Avoids ad-hoc strategy wiring later.

Output:
- Stable strategy abstraction ready for exchange connection.

### Step D2 - Build exchange-strategy integration harness
What:
- Connect strategy layer to exchange gateway methods.
- Ensure strategy can submit/cancel/query through the defined API.

Why:
- Strategy is only meaningful if it can interact with exchange simulator.
- Early harness reduces integration surprises.

Output:
- Working strategy-to-exchange interaction path.

### Step D3 - Build first end-to-end smoke flow
What:
- Connect full chain:
  - replay events -> exchange -> strategy callbacks
- Assert expected behavior in at least one controlled scenario.

Why:
- Validates that module boundaries are actually compatible.
- Gives team a real "system works" proof early.

Output:
- First complete integration test demonstrating end-to-end flow.

### Step D4 - Enforce CI quality guardrails
What:
- Keep test suite deterministic and fast.
- Ensure every PR is validated in CI.
- Add/maintain regression tests for bugs found during integration.

Why:
- CI is your team's safety net.
- Prevents broken merges and hidden contract drift.

Output:
- Reliable CI checks that support fast, safe iteration.

### Step D5 - Run contract sync and integration checkpoints
What:
- At weekly checkpoints, verify:
  - capture format still matches replay parser
  - replay event schema still matches exchange expectations
  - exchange gateway still matches strategy integration
- Report mismatches immediately.

Why:
- Integration failures are cheaper to fix early than late.

Output:
- Stable multi-person development with fewer blocking conflicts.

### Step D6 - Keep integration narrative for TA demos
What:
- Maintain short weekly notes:
  - what integration broke
  - what contract changed
  - what tests were added to prevent recurrence

Why:
- Shows engineering maturity and control over system complexity.

Output:
- Clear semester-long integration story for reports/presentations.

## TA explanation template (use this all semester)
When asked "What did you do and why?", answer in this format:

1. **Objective**: "I integrated replay, exchange, and strategy into one testable pipeline."
2. **Implementation**: "I built the strategy interface, wired exchange gateway calls, and added end-to-end tests."
3. **Reason**: "Without integration ownership, independent modules pass alone but fail together."
4. **Evidence**: "CI passes cross-module smoke tests and regression tests for prior integration issues."
5. **Impact**: "The team can now evaluate strategy behavior on a reproducible full-system path."

## Deliverables checklist for Person D
- [ ] Strategy interface and baseline no-op strategy
- [ ] Strategy <-> exchange integration harness
- [ ] End-to-end replay->exchange->strategy smoke tests
- [ ] CI checks reliable and deterministic
- [ ] Contract mismatch checks at weekly checkpoints
- [ ] Weekly notes ready for TA explanation

---

## 7) Integration checkpoints (required cadence)

To avoid last-minute merge chaos, use this schedule every week:

1. **Monday**: lock interface tasks for the week.
2. **Wednesday**: mid-week integration branch merge.
3. **Friday**: end-week full integration + demo run.

At each checkpoint:
- run `pytest -q`
- verify no contract drift
- verify docs still reflect real behavior

---

## 8) Week-by-week execution map

## Week 1 (already in progress)
- Baseline structure, CI, tests, docs, capture stub.

## Week 2
- A: real websocket capture + queue + writer + rotation/snapshots.
- B: replay parser foundations + deterministic ordering core.
- C: exchange domain objects + order book scaffold.
- D: strategy interface + cross-module integration tests skeleton.

## Week 3
- A: reliability hardening + S3 upload flow.
- B: normalized events + replay stability tests.
- C: matching rules baseline (GTC/IOC/Post-only scaffold).
- D: end-to-end replay->exchange->strategy smoke path in CI.

---

## 9) Handoff contracts between people

These are the key dependencies:

- **A -> B**: capture file format and naming conventions.
- **B -> C**: normalized market event stream contract.
- **C -> D**: strategy gateway API and fill/account events.
- **D -> Team**: integration feedback and contract mismatch reports.

Rule:
- No one silently changes handoff formats.
- Changes require a short design note and team approval.

---

## 10) Acceptance checklist per person

A task is accepted only if:
- Code works locally.
- Tests cover expected behavior.
- CI passes.
- Docs reflect final behavior.
- Another teammate can run it without guessing.

---

## 11) Recommended communication style

- Keep daily updates short:
  - what was built
  - what is blocked
  - what contract changed
- Post contract changes before merging.
- Prefer small PRs over large weekly dumps.

---

## 12) Final note

This partition is intentionally interface-first.  
Parallel work is good only when contracts are stable.

If you keep these boundaries and integration cadence, your 4-person team can move quickly without
breaking each other every sprint.
