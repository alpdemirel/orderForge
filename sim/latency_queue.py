"""
Latency queue for simulating network delay on strategy orders/cancels.

Strategy orders don't land on the exchange instantly.  This module holds
them in a min-heap keyed by their predicted landing timestamp and drains
them when the simulation clock advances past that point.

Design from docs/NORMALIZED_EVENT_MAPPING.md §5.5.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, List


@dataclass(order=True)
class _PendingAction:
    """Heap entry — orders/cancels sorted by landing time, then sequence."""
    landing_ts_ms: int
    seq: int = field(compare=True)
    action: str = field(compare=False)          # "submit" | "cancel"
    payload: object = field(compare=False)       # Order or order_id str


class LatencyQueue:
    """Min-heap of pending strategy actions keyed by ``landing_ts_ms``.

    Usage::

        lq = LatencyQueue(submit_fn=engine.submit_order,
                          cancel_fn=engine.cancel_order)

        # Strategy submits an order — goes into the queue, not the engine.
        lq.enqueue_submit(order, landing_ts_ms)

        # Each replay tick, drain everything that has "arrived".
        lq.drain(current_exch_ts_ms)
    """

    def __init__(
        self,
        submit_fn: Callable,
        cancel_fn: Callable,
    ) -> None:
        self._heap: List[_PendingAction] = []
        self._seq: int = 0
        self._submit_fn = submit_fn
        self._cancel_fn = cancel_fn
        self._results: list = []          # filled by drain, read by caller

    # ── Enqueue ──────────────────────────────────────────────────────────

    def enqueue_submit(self, order, landing_ts_ms: int) -> None:
        entry = _PendingAction(
            landing_ts_ms=landing_ts_ms,
            seq=self._seq,
            action="submit",
            payload=order,
        )
        heapq.heappush(self._heap, entry)
        self._seq += 1

    def enqueue_cancel(self, order_id: str, landing_ts_ms: int) -> None:
        entry = _PendingAction(
            landing_ts_ms=landing_ts_ms,
            seq=self._seq,
            action="cancel",
            payload=order_id,
        )
        heapq.heappush(self._heap, entry)
        self._seq += 1

    # ── Drain ────────────────────────────────────────────────────────────

    def drain(self, current_ts_ms: int) -> list:
        """Pop and execute every action whose landing time ≤ *current_ts_ms*.

        Returns a list of ``ExecutionResult`` objects from submitted orders
        (cancel results are not tracked).
        """
        results = []
        while self._heap and self._heap[0].landing_ts_ms <= current_ts_ms:
            entry = heapq.heappop(self._heap)
            if entry.action == "submit":
                result = self._submit_fn(entry.payload)
                results.append(result)
            elif entry.action == "cancel":
                self._cancel_fn(entry.payload)
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    def pending_count(self) -> int:
        return len(self._heap)

    def cancel_inflight(self, order_id: str) -> bool:
        """Remove a pending submit that hasn't landed yet (local cancel).

        Returns True if the order was found and removed.
        """
        for i, entry in enumerate(self._heap):
            if entry.action == "submit" and getattr(entry.payload, "order_id", None) == order_id:
                self._heap[i] = self._heap[-1]
                self._heap.pop()
                heapq.heapify(self._heap)
                return True
        return False
