# Normalized Event Mapping (Bitfinex L3 -> OrderForge)

This document defines the exact stream contract mapping from raw Bitfinex L3 websocket events (add, update, remove) to the normalized exchange order book operations within OrderForge.

## 1. Raw Bitfinex L3 Stream (Input)

Bitfinex sends Level 3 (R0) order book updates as JSON arrays over its WebSocket API.
The events fall into two primary categories:

### A. Snapshot (`bf_snapshot`)
A snapshot contains the entire state of the order book upon initial connection.
- **Format:** `[CHANNEL_ID, [ [ORDER_ID, PRICE, AMOUNT], ... ] ]`
- **Fields:**
  - `ORDER_ID` (int): Unique identifier assigned by Bitfinex.
  - `PRICE` (float): The exact price level.
  - `AMOUNT` (float): Defines both the size and the side of the order.
    - `> 0`: Bid (Buy order)
    - `< 0`: Ask (Sell order)

### B. Updates (`bf_update`)
Individual additions, modifications, or cancellations to the order book.
- **Format:** `[CHANNEL_ID, ORDER_ID, PRICE, AMOUNT]`
- **Removal Flag:** Bitfinex denotes order removals/cancellations and full-fills by setting the `PRICE` to `0`.

---

## 2. Normalized Dictionary Structure (Intermediate)

Before touching the generic `ExchangeEngine` logic, OrderForge transforms the raw arrays into a sanitized, standardized dictionary within `market_data/reader.py` (specifically `extract_bitfinex_update`).

| Raw Payload Field | Normalized Key | Normalization Logic |
| :--- | :--- | :--- |
| `ORDER_ID` | `"order_id"` | Cast to `int` |
| `PRICE` | `"price"` | Cast to `float`. Used as indicator for `"removed"` status. |
| `AMOUNT` | `"amount"` | Cast to `float` (magnitude only used downstream). |
| `AMOUNT` sign | `"side"` | `"bid"` if `AMOUNT > 0`, `"ask"` if `AMOUNT < 0`. `None` if removed. |
| `PRICE == 0` | `"removed"` | `True` if `PRICE == 0`, else `False`. |

---

## 3. OrderBook Operations Mapping (Output)

The mapped dictionaries are applied to the `OrderBook` in `exchange/market_event_applicator.py`. Bitfinex L3 events act as passive liquidity injections. 

OrderForge treats all market data as `Order` objects owned by the special `MARKET_OWNER_ID` (`"__market__"`).

### Scenario 1: Order Add (New Order)
- **Trigger:** `"removed": False` and `"order_id"` is not currently in the internal map.
- **Operation:**
  1. Generate internal ID: `f"mkt-{bitfinex_id}"`.
  2. Create an `Order` object:
     - `price`: Fixed-point integer conversion `round(price * PRICE_MULTIPLIER)`
     - `quantity`: Fixed-point integer conversion `round(abs(amount) * QUANTITY_MULTIPLIER)`
     - `side`: `Side.BUY` (bid) or `Side.SELL` (ask)
     - `tif`: `TimeInForce.GTC`
  3. Call `book.add(order)`.
  4. Save relationship in `_bitfinex_id_to_internal_id`.

### Scenario 2: Order Remove (Cancel / Filled)
- **Trigger:** `"removed": True` (`PRICE == 0`).
- **Operation:**
  1. Look up `internal_id` from `_bitfinex_id_to_internal_id`.
  2. If found, call `book.cancel(internal_id)`.
  3. Delete reference from map.

### Scenario 3: Order Update (Modify)
- **Trigger:** `"removed": False` but `"order_id"` is **already** in the internal map.
- **Operation:** OrderForge `OrderBook` primarily supports `.add()` and `.cancel()`. A modification is handled as an atomic replace.
  1. Call `book.cancel(internal_id)` to remove the old resting order.
  2. Perform the exact same steps in **Scenario 1** to `.add()` the new order with the updated price/quantity.

---

## 4. Time Synchronization (Dual-Timestamps)
All the above mappings happen under the hood of `MarketEvent` routing.
In the actual stream:
- `local_ts_ns` dictates **when** the event is processed by the Replayer.
- `exchange_ts_ns` represents the actual physical occurrence time at Bitfinex.

The mapping ensures that the normalized operations (`add` or `cancel`) are applied at the precise chronological point in the discrete-event clock.

Every captured market update message carries both timestamps in the raw log line:

```
<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
```

- `local_ts_ns` is stamped by the capture process on the EC2 host the instant the WebSocket frame is received (before any parsing).
- `exchange_ts_ns` is the exchange-side event timestamp, obtained via the Bitfinex TIMESTAMP conf flag (32768). When enabled, every array message has a trailing millisecond timestamp (`mts`) appended. `classify_bitfinex_msg()` in `capture/main.py` extracts `mts`, converts it to nanoseconds, and strips it from `raw_json` before writing.

For legacy data captured before the TIMESTAMP flag was enabled, `exchange_ts_ns` is 0 and the Replayer synthesises it using `default_latency_ms`.

Because the EC2 instance is colocated in the same AWS region and AZ as Bitfinex's servers (~5.6ms measured latency), the two clocks are tightly synchronized and cross-clock comparisons are valid with sub-millisecond drift.

---

## 5. Latency Emulation Model (Hist → Prod Parity)

### 5.1  Production flow (reference)

In live trading the data flow is:

```
exchange ─[md update]→ strategy ─[order / cancel]→ exchange ─[ack / fill]→ strategy
```

Concretely:

1. The MD receiver receives a market data update from the exchange, parses it, and forwards it to the strategy. *(MD receiver and strategy are assumed to run in the same process for simplicity; the process topology on the same box does not materially affect the hist model.)*
2. The strategy receives the update, does calculations, and submits new orders or cancels to the exchange via the OGW. *(OGW and strategy are likewise assumed to be the same process.)*
3. The exchange receives the request, matches or places it on the book, and sends an acknowledgement back.
4. The strategy receives the ack asynchronously and updates its internal state.

The primary trading loop is driven by market data updates; everything else is a consequence of step 1.

### 5.2  The strategy's internal clock

From the strategy's point of view **`local_ts` is "now"**. Every time the strategy receives a new market data update it sets its internal clock:

```python
self.current_local_ts = event.local_ts_ms   # set at top of on_market_update()
```

All timestamps the strategy reasons about (order urgency, quoting spread, risk windows) are relative to this value.

### 5.3  Latency prediction

Each tick the strategy observes:

```
observed_latency_ms = local_ts - exchange_ts   # exchange → strategy one-way latency
```

This is the latency for the **inbound** leg. The strategy assumes the **outbound** leg (strategy → exchange) will have the same latency on the next tick — a reasonable first-order approximation because exchange and network latencies are highly autocorrelated:

```python
self.predicted_latency_ms = self.current_local_ts - event.exchange_ts_ms
```

### 5.4  Order landing timestamp

When the strategy submits an order, the estimated exchange landing time is:

```
landing_ts_ms = current_local_ts + predicted_latency_ms
```

**Example:**

| Step | Value |
|------|-------|
| Exchange sends MD update | `exchange_ts = 100 ms` |
| Strategy receives it | `local_ts = 110 ms` |
| Observed latency | `110 − 100 = 10 ms` |
| Strategy clock | `current_local_ts = 110 ms` |
| Strategy submits order | — |
| Predicted landing time | `110 + 10 = 120 ms` |

The order must "land" at the exchange at `exchange_ts = 120 ms`, not at `110 ms`.

### 5.5  Exchange simulator heap

To emulate this, the exchange simulator maintains a **min-heap of pending strategy orders keyed by `landing_ts_ms`**:

```
heap entries: (landing_ts_ms, seq, order)
```

On each replay tick, the Replayer calls `LatencyQueue.drain(current_exch_ts_ms)` **before** applying any book mutations for that tick. `drain()` pops and lands every order whose `landing_ts_ms ≤ current_exch_ts_ms`:

```python
while heap and heap[0].landing_ts_ms <= current_exch_ts_ms:
    order = heapq.heappop(heap)
    exchange.submit_order(order)
```

This ensures strategy orders are visible to the matching engine at the correct simulated time, not at submission time.

Cancels of **in-flight** orders (submitted but not yet landed) are discarded without latency — the cancel races the order before it leaves the local process. Cancels of **already-landed** resting orders travel through a separate cancel heap with the same one-way predicted latency, since the cancel request must traverse the same network path.

Ack delivery back to the strategy fires immediately after `submit_order` returns. Ack latency is less critical to model correctly than order-landing latency, because the strategy's next meaningful action is gated on the next market data update, not the ack.

### 5.6  Implementation references

| Component | File |
|-----------|------|
| Latency queue (heap + drain) | `sim/latency_queue.py` |
| Pipeline wiring | `sim/main.py` — `build_pipeline()` |
| Strategy clock update | `strategy/base.py` — `update_clock()` |
| Replayer drain call | `market_data/replayer.py` — `run()` tick loop |
