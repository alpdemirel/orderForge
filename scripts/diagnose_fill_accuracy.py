"""
Deep fill-accuracy diagnostics — analyses the realistic-ness of simulated
fills, not just book reconstruction.

1. Quote-vs-latency ratio: how often do quotes survive to the book?
2. Adverse selection: does the mid move against us after fills?
3. Fill timing: are fills clustering or evenly distributed?
4. Cancel-fill race: how often do orders get filled during cancel latency?
5. Latency distribution: how variable is observed latency?
"""

import math
import sys
import os
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER, Side, MARKET_OWNER_ID
from market_data.replayer import Replayer
from market_data.reader import read_capture_file
from sim.latency_queue import LatencyQueue
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy

import logging
logging.basicConfig(level=logging.WARNING)

EVENTS_FILE = "data/2026-03-29/events.log"


class InstrumentedStrategy(AvellanedaStoikovStrategy):
    """AS strategy with fill-accuracy instrumentation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fill_records = []          # (fill, mid_at_fill, mid_5_ticks_later)
        self.quote_count = 0
        self.quote_survived_count = 0   # quotes that were NOT cancelled inflight
        self.latencies_observed = []
        self.mid_history = []           # (ts_ms, mid)
        self._pending_adverse = []      # fills waiting for future mid to measure AS
        self._ticks_since_fill = defaultdict(int)
        self.cancel_fail_count = 0      # orders filled during cancel window
        self.tick_intervals = []

    def on_market_data(self, event):
        mid = (event["best_bid"] + event["best_ask"]) / 2.0
        ts = event["local_ts_ms"]

        # Record tick interval
        if self.mid_history:
            self.tick_intervals.append(ts - self.mid_history[-1][0])

        self.mid_history.append((ts, mid))

        # Measure adverse selection: check fills from 5+ ticks ago
        resolved = []
        for i, (fill, fill_mid, fill_ts, side) in enumerate(self._pending_adverse):
            ticks_elapsed = self._ticks_since_fill[id(fill)]
            self._ticks_since_fill[id(fill)] += 1
            if ticks_elapsed >= 50:  # measure after 50 ticks
                self.fill_records.append({
                    "fill_mid": fill_mid,
                    "mid_after": mid,
                    "side": side,
                    "fill_ts": fill_ts,
                    "after_ts": ts,
                    "price": fill.price / PRICE_MULTIPLIER,
                })
                resolved.append(i)

        for i in reversed(resolved):
            f = self._pending_adverse.pop(i)
            del self._ticks_since_fill[id(f[0])]

        # Track latency
        if self.current_latency_estimate > 0:
            self.latencies_observed.append(self.current_latency_estimate)

        # Check if previous quotes survived (were on the book)
        if self._latency_queue is not None:
            for oid in self._active_order_ids:
                # If cancel_inflight returns True, the order never reached the book
                # We check by seeing if the order exists on the exchange
                order = self.exchange.get_order(oid)
                if order is not None:
                    self.quote_survived_count += 1

        # Call parent (which cancel-replaces quotes)
        super().on_market_data(event)
        self.quote_count += 1

    def on_order_fill(self, fill_event):
        super().on_order_fill(fill_event)

        # Record fill for adverse selection analysis
        if self.mid_history:
            fill_mid = self.mid_history[-1][1]
            fill_ts = self.mid_history[-1][0]
        else:
            fill_mid = fill_event.price / PRICE_MULTIPLIER
            fill_ts = 0

        if fill_event.maker_owner_id == "default":
            side = fill_event.maker_side
        elif fill_event.taker_owner_id == "default":
            side = fill_event.taker_side
        else:
            return

        self._pending_adverse.append((fill_event, fill_mid, fill_ts, side))
        self._ticks_since_fill[id(fill_event)] = 0


def run():
    strategy = InstrumentedStrategy(
        risk_aversion=0.001,
        order_size=0.01,
        session_duration=86400.0,
    )

    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway, on_fill=strategy.on_order_fill)
    strategy.connect_exchange(notifying)

    latency_queue = LatencyQueue(
        submit_fn=notifying.submit_order,
        cancel_fn=notifying.cancel_order,
    )
    strategy.connect_latency_queue(latency_queue)

    replayer = Replayer(
        EVENTS_FILE, strategy, engine=engine,
        on_fill=notifying.emit_fill,
        latency_queue=latency_queue,
    )

    print(f"Running instrumented backtest on {EVENTS_FILE}...")
    t0 = time.time()
    replayer.run()
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    fills = notifying.emitted_fills

    # ═══════════════════════════════════════════════════════════════════════
    # 1. LATENCY DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  1. LATENCY DISTRIBUTION")
    print("=" * 70)

    lats = strategy.latencies_observed
    if lats:
        lats_sorted = sorted(lats)
        n = len(lats_sorted)
        print(f"  Samples:    {n:,}")
        print(f"  Min:        {lats_sorted[0]} ms")
        print(f"  P10:        {lats_sorted[n // 10]} ms")
        print(f"  P25:        {lats_sorted[n // 4]} ms")
        print(f"  Median:     {lats_sorted[n // 2]} ms")
        print(f"  P75:        {lats_sorted[3 * n // 4]} ms")
        print(f"  P90:        {lats_sorted[9 * n // 10]} ms")
        print(f"  P99:        {lats_sorted[int(n * 0.99)]} ms")
        print(f"  Max:        {lats_sorted[-1]} ms")
        mean_lat = sum(lats) / n
        var_lat = sum((l - mean_lat) ** 2 for l in lats) / n
        std_lat = math.sqrt(var_lat)
        print(f"  Mean:       {mean_lat:.1f} ms")
        print(f"  Std dev:    {std_lat:.1f} ms")
        print(f"  CV (std/mean): {std_lat / mean_lat:.2f}")

        # Is it variable enough to matter?
        if std_lat / mean_lat > 0.3:
            print(f"\n  ⚠ HIGH VARIABILITY — point estimate misses {std_lat:.0f}ms of spread")
            print(f"    Impact: orders land ±{std_lat:.0f}ms from predicted time")
            print(f"    Fix: sample from observed distribution instead of point estimate")
        else:
            print(f"\n  Latency is stable — point estimate is adequate")

    # ═══════════════════════════════════════════════════════════════════════
    # 2. QUOTE SURVIVAL RATE
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  2. QUOTE SURVIVAL (do quotes reach the book?)")
    print("=" * 70)

    print(f"  Total quote cycles:      {strategy.quote_count}")
    print(f"  Quotes on book at check: {strategy.quote_survived_count}")
    # Each cycle places 2 orders (bid + ask)
    total_orders = strategy.quote_count * 2
    survival_rate = strategy.quote_survived_count / total_orders * 100 if total_orders else 0
    print(f"  Survival rate:           {survival_rate:.1f}%")

    if strategy.tick_intervals:
        ti = strategy.tick_intervals
        avg_interval = sum(ti) / len(ti)
        median_interval = sorted(ti)[len(ti) // 2]
        print(f"\n  Tick interval — mean: {avg_interval:.1f}ms, median: {median_interval}ms")
        if lats:
            print(f"  Latency / tick interval: {mean_lat / avg_interval:.2f}x")
            if mean_lat > avg_interval:
                print(f"  ⚠ Latency > tick interval — most quotes are cancelled locally before landing")
            else:
                print(f"  Quotes typically survive {avg_interval / mean_lat:.1f} ticks before replacement")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. ADVERSE SELECTION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  3. ADVERSE SELECTION (does mid move against us after fills?)")
    print("=" * 70)

    records = strategy.fill_records
    if records:
        buy_adverse = []
        sell_adverse = []
        for r in records:
            mid_change = r["mid_after"] - r["fill_mid"]
            if r["side"] == Side.BUY:
                # Bought — adverse if mid dropped
                buy_adverse.append(mid_change)
            else:
                # Sold — adverse if mid rose
                sell_adverse.append(-mid_change)  # negative = adverse

        all_adverse = buy_adverse + sell_adverse

        print(f"  Fills analysed: {len(records)} (measured 50 ticks after fill)")
        if buy_adverse:
            avg_buy = sum(buy_adverse) / len(buy_adverse)
            pct_buy_neg = sum(1 for a in buy_adverse if a < 0) / len(buy_adverse) * 100
            print(f"\n  BUY fills ({len(buy_adverse)}):")
            print(f"    Avg mid change after buy:  ${avg_buy:+.4f}")
            print(f"    % where mid dropped:       {pct_buy_neg:.1f}%")
        if sell_adverse:
            avg_sell = sum(sell_adverse) / len(sell_adverse)
            pct_sell_neg = sum(1 for a in sell_adverse if a < 0) / len(sell_adverse) * 100
            print(f"\n  SELL fills ({len(sell_adverse)}):")
            print(f"    Avg mid change after sell: ${avg_sell:+.4f} (negative = moved against)")
            print(f"    % where mid rose:          {pct_sell_neg:.1f}%")

        if all_adverse:
            avg_all = sum(all_adverse) / len(all_adverse)
            pct_adverse = sum(1 for a in all_adverse if a < 0) / len(all_adverse) * 100
            print(f"\n  OVERALL:")
            print(f"    Avg post-fill mid move:    ${avg_all:+.4f}")
            print(f"    % adversely selected:      {pct_adverse:.1f}%")
            if pct_adverse > 55:
                print(f"    ⚠ Significant adverse selection detected")
                print(f"      This means fills tend to happen right before the price moves against us.")
                print(f"      Fix: model fill probability as decreasing when aggressor has info advantage")
            else:
                print(f"    Adverse selection within normal range")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. FILL TIMING DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  4. FILL TIMING & CLUSTERING")
    print("=" * 70)

    if len(fills) >= 2:
        fill_times = []
        for f in fills:
            # Use fill price as proxy for timing (fills at same price = clustered)
            fill_times.append(f.price / PRICE_MULTIPLIER)

        # Inter-fill intervals (using mid_history timestamps)
        fill_ts_list = []
        for r in strategy.fill_records:
            fill_ts_list.append(r["fill_ts"])

        if len(fill_ts_list) >= 2:
            intervals = [fill_ts_list[i+1] - fill_ts_list[i]
                         for i in range(len(fill_ts_list) - 1) if fill_ts_list[i+1] > fill_ts_list[i]]
            if intervals:
                intervals_sorted = sorted(intervals)
                n = len(intervals_sorted)
                print(f"  Inter-fill intervals ({n} pairs):")
                print(f"    Min:    {intervals_sorted[0]:,.0f} ms")
                print(f"    P10:    {intervals_sorted[n // 10]:,.0f} ms")
                print(f"    Median: {intervals_sorted[n // 2]:,.0f} ms")
                print(f"    P90:    {intervals_sorted[9 * n // 10]:,.0f} ms")
                print(f"    Max:    {intervals_sorted[-1]:,.0f} ms")

                # Check for burst clustering
                burst_threshold = intervals_sorted[n // 10]  # P10
                bursts = sum(1 for i in intervals if i <= burst_threshold)
                print(f"\n    Burst fills (<{burst_threshold}ms apart): {bursts} ({bursts/n:.1%})")

        # Maker vs taker ratio
        maker = sum(1 for f in fills if f.maker_owner_id == "default")
        taker = sum(1 for f in fills if f.taker_owner_id == "default")
        print(f"\n  Maker fills: {maker} ({maker/len(fills):.1%})")
        print(f"  Taker fills: {taker} ({taker/len(fills):.1%})")
        if taker == 0:
            print(f"  ⚠ 0% taker fills — strategy never crosses the spread")
            print(f"    In production, stale quotes landing after price moves would sometimes cross")
            print(f"    This suggests latency is low relative to price movement")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. STALE QUOTE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  5. QUOTE STALENESS (how far is fill price from mid at fill time?)")
    print("=" * 70)

    if strategy.fill_records:
        deviations = []
        for r in strategy.fill_records:
            dev = abs(r["price"] - r["fill_mid"])
            deviations.append(dev)

        deviations_sorted = sorted(deviations)
        n = len(deviations_sorted)
        print(f"  Fill-to-mid deviation ({n} fills):")
        print(f"    Min:    ${deviations_sorted[0]:.2f}")
        print(f"    P25:    ${deviations_sorted[n // 4]:.2f}")
        print(f"    Median: ${deviations_sorted[n // 2]:.2f}")
        print(f"    P75:    ${deviations_sorted[3 * n // 4]:.2f}")
        print(f"    Max:    ${deviations_sorted[-1]:.2f}")
        avg_dev = sum(deviations) / n
        print(f"    Mean:   ${avg_dev:.2f}")

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  ACCURACY IMPROVEMENT OPPORTUNITIES")
    print("=" * 70)


if __name__ == "__main__":
    run()
