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
- `local_ts_ns` dictates **when** the event is un-queued from the Replayer priority queue.
- `exchange_ts_ns` represents the actual physical occurrence time at Bitfinex.

The mapping ensures that the normalized operations (`add` or `cancel`) are applied at the precise chronological point in the discrete-event clock.
