A) Alp — Data Pipeline & Ops
Your job is foundational. If you don't deliver data, nobody else can validate anything.

1. Pull and distribute sample data from S3
Download at minimum 2 full days of:

events.log (the incremental L3 WebSocket stream, pipe-delimited)
All snapshot_HHMMSS.log files for those days (the 10-minute JSON snapshots)
Share with the team locally. Top priority — do this first.

2. Gap analysis on events.log
Write a script that scans the event logs and reports:

Reconnect events: any bf_subscribed line mid-file means a disconnect happened → book state is stale until the next bf_snapshot following it
Timestamp jumps: consecutive events where local_ts_ns gap is > 1 second (suspicious)
Coverage: what time range does each day's log actually cover?
This matters because Tan's replay must skip or handle reconnect gaps correctly. Document findings.

3. Verify snapshot-to-events alignment
For each snapshot_HHMMSS.log, confirm the corresponding events.log has coverage starting from before that snapshot's timestamp. Tan needs to be able to say: "start from snapshot_044808.log, then replay events.log from 04:48:08 forward." Confirm this is possible for at least 2 full days.

4. Health check alarm
A cron or systemd timer that checks the modification time of the current day's events.log on EC2. If it hasn't been written to in more than 5 minutes, send an alert (email or log to a file that can be monitored). The service has reconnect/backoff logic already — this alarm catches cases where the entire process dies.

B) Tan — Discrete-Event Simulation Loop (Most Critical Deliverable)
run_historical_sim.py was listed as done in the presentation but does not exist in the repo. This is blocking everyone. Your primary output this week is a working, committed simulation loop.

The architecture you need to implement:

The heap contains two event types, both keyed by exchange timestamp:


MinHeap entry: (exchange_ts, event)

Where event is either:
  - MarketEvent (from events.log replay)
  - OrderLandingEvent(order, owner) — a strategy order scheduled to land at a future exchange_ts
The sim loop:


while heap not empty:
    exch_ts, event = heap.pop()

    if isinstance(event, MarketEvent):
        applicator.apply(event)                               # update passive LOB
        strategy.update_clock(event.exchange_ts_ns,
                               event.local_ts_ns)             # update latency estimate
        strategy.on_market_data(event)                        # strategy may submit orders

    elif isinstance(event, OrderLandingEvent):
        result = engine.submit_order(event.order)             # match at correct future LOB state
        strategy.on_order_fill(result)                        # ack back to strategy
The OrderLandingEvent scheduling: When strategy.on_market_data() triggers gateway.submit_order(order), the gateway should NOT match immediately. In simulation mode it must:

Compute landing_ts = strategy.current_local_ts + strategy.current_latency_estimate
Push OrderLandingEvent(order, landing_ts) onto the heap
Return without matching
This is the critical piece that makes the backtest fidelity correct. The order lands at exchange time 120ms, at which point the LOB reflects all real market events up to 120ms — so your order gets matched against the real market state at the predicted arrival time, not the stale state from when the strategy decided to trade.

How to implement the gateway intercept cleanly:
Add a "simulation mode" to ExchangeGateway:


class ExchangeGateway:
    def __init__(self, engine=None, sim_heap=None):
        self._engine = engine or ExchangeEngine()
        self._sim_heap = sim_heap  # None = live mode, heap = sim mode

    def submit_order(self, order, scheduled_landing_ts=None):
        if self._sim_heap is not None:
            self._sim_heap.push(scheduled_landing_ts, OrderLandingEvent(order))
        else:
            return self._engine.submit_order(order)   # live/test mode unchanged
Additional tasks:

load_snapshot_file(path) — reads snapshot_HHMMSS.log (pure JSON dict {order_id_str: {price, amount}}) and returns it as a dict for Bora's apply_s3_snapshot(). This is a one-function addition to market_data/reader.py.
Build the snapshot comparison framework: load snapshot A → replay events forward to snapshot B's timestamp → compare best bid/ask → record match rate.
Validate deterministic replay: same day, run twice, assert identical sequence of heap pops and fill outcomes.


C) Bora
Your work is the passive LOB maintenance layer. The sim loop calls your code when processing market events; you keep the book in the correct state so that when strategy orders land, they match against real market depth.

1. exchange/market_event_applicator.py

As detailed before — no changes to the core design. The interface apply(event: MarketEvent) is exactly what the sim loop calls when it pops a MarketEvent from the heap.

Key behaviors:

bf_snapshot → _clear_market_orders() + add all orders from snapshot payload
bf_update with price=0 → remove order
bf_update with price>0, new id → add order
bf_update with price>0, existing id → cancel + re-add (modify)
All other channels → no-op
Maintain _bitfinex_id_to_internal_id: dict[int, str] mapping. Internal order IDs are "mkt-{bitfinex_id}". All market orders tagged owner_id="__market__".

2. apply_s3_snapshot(data: dict) method on MarketEventApplicator

For seeding the LOB from a snapshot_HHMMSS.log file (the JSON format from S3). The sim loop would call this once at startup before beginning the event replay:


def apply_s3_snapshot(self, snapshot_data: dict) -> None:
    # snapshot_data = {"233421046209": {"price": 73988, "amount": 0.0024}, ...}
    self._clear_market_orders()
    for order_id_str, entry in snapshot_data.items():
        self._insert_market_order(
            bitfinex_id=int(order_id_str),
            float_price=entry["price"],
            float_amount=entry["amount"],
            side_str="bid" if entry["amount"] > 0 else "ask",
        )
Both paths (apply() for the stream, apply_s3_snapshot() for the snapshot file) share the same _insert_market_order() internals — no duplication.

3. Add to ExchangeEngine and ExchangeGateway

engine.apply_market_event(event) — lazy-init applicator, delegate
engine.apply_s3_snapshot(data) — delegate to applicator
Same one-liner passes in gateway
4. Regression tests — tests/test_market_event_applicator.py

Use real S3 snapshot data once Alp provides it. Also use the existing tests/fixtures/events_l3_sample.log for the WebSocket stream path.

Core tests to cover:

Snapshot loads correct order count, correct best bid/ask (verify prices after * PRICE_MULTIPLIER)
apply_s3_snapshot() with real S3 snapshot data → verify best bid, best ask, order count
Unknown removal is graceful (no crash)
Update adds/modifies/removes correctly
Strategy orders survive a snapshot clear (only __market__ orders are cleared)
End-to-end: load snapshot → submit strategy SELL → fill recorded against __market__ bid → fill.maker_owner_id == "__market__"
5. Add a small S3-format fixture file
tests/fixtures/snapshot_sample.json — a slice of the real S3 snapshot (20-30 orders, both bids and asks). This becomes the canonical regression fixture for the snapshot path and can be checked into the repo without depending on network access.

D) Kaan — A-S Strategy + End-to-End Integration
You're partially dependent on Bora (LOB bridge) and Tan (sim loop) being ready. Do the strategy math independently first, then wire.

1. Implement Avellaneda-Stoikov quoting logic

Fill in strategy/avellaneda_stoikov.py:


def calculate_reservation_price(self, mid_price: float, time_left: float) -> float:
    # r = mid - inventory * gamma * sigma^2 * time_left
    return mid_price - self.inventory_position * self.risk_aversion * (self.sigma ** 2) * time_left

def calculate_optimal_spread(self, time_left: float) -> float:
    # delta = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/k)
    import math
    return (self.risk_aversion * self.sigma ** 2 * time_left
            + (2 / self.risk_aversion) * math.log(1 + self.risk_aversion / self.k))

def on_market_data(self, event) -> None:
    # Update sigma from mid-price change, recalculate quotes, submit bid/ask
    ...

def on_order_fill(self, fill_event) -> None:
    # Update inventory_position based on fill side/quantity
    ...
For sigma: start simple — use an exponentially weighted moving average of absolute mid-price changes. The mid-price at each tick is (best_bid + best_ask) / 2 / PRICE_MULTIPLIER (convert from ticks back to USD for the math).

2. Wire the complete chain (once Tan's sim loop is ready)


S3 snapshot_HHMMSS.log
  → gateway.apply_s3_snapshot(data)           # seed LOB

events.log (from that timestamp forward)
  → read_capture_file()                        # yields MarketEvents
  → heap.push(event.exchange_ts_ns, event)     # seed heap with all MD events

sim_loop.run()
  → pops heap by exchange_ts
  → MarketEvent → applicator.apply() + strategy.update_clock() + strategy.on_market_data()
  → OrderLandingEvent → engine.submit_order() at correct future LOB state + strategy.on_order_fill()
3. End-to-end integration test

Run a 10-minute segment (from one snapshot to the next). Assert:

Orders are submitted (A-S generates bid/ask quotes each tick)
At least some fills are recorded when the market crosses your quotes
Account state updates correctly (position and cash change after fills)
Running the same segment twice produces identical fill sequences
4. CI

All tests pass: existing 27 tests + Bora's new 12 tests + your new integration tests.

Dependency Graph

Alp (data from S3)
    └──> Bora (S3 snapshot fixture for regression tests)
    └──> Tan (real data to validate replay against)
    └──> Kaan (multi-day dataset for strategy testing)

Bora (MarketEventApplicator)
    └──> Tan (sim loop calls applicator.apply())
    └──> Kaan (end-to-end fill tests need LOB populated)

Tan (sim loop + heap + OrderLandingEvent)
    └──> Kaan (full chain wiring depends on sim loop existing)

Kaan (A-S math) — independent, start now
Kaan (full chain wiring) — blocked on Bora + Tan
Critical path: Alp → (data) → Bora builds snapshot fixture → regression tests pass. Tan builds sim loop → Kaan wires full chain. These two tracks can run in parallel. The only hard blocker is Tan's sim loop for Kaan's end-to-end tests.