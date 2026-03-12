"""
Minimal Integration Harness
Orchestrates the interaction between the Strategy and Exchange modules.
"""
import os
import sys

# Add the project root to the Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from strategy.noop import NoOpStrategy
from exchange import ExchangeGateway

def run_simulation() -> None:
    print("[run_sim] Initializing exchange gateway...")
    gateway = ExchangeGateway()

    print("[run_sim] Initializing NoOpStrategy...")
    strategy = NoOpStrategy()

    print("[run_sim] Connecting strategy directly to exchange...")
    strategy.connect_exchange(gateway)

    exch_ts = 1000
    local_ts = 1015
    print(f"[run_sim] Updating strategy clock with exch_ts={exch_ts}, local_ts={local_ts}...")
    strategy.update_clock(exch_ts=exch_ts, local_ts=local_ts)
    print(f"[run_sim] Current latency estimate: {strategy.current_latency_estimate}ms")

    print("[run_sim] Submitting a sample buy order through the strategy...")
    strategy.submit_order(price=70_000, side="buy", amount=1, order_type="GTC")

    print(f"\n[run_sim] Advancing exchange time to allow order to land...")
    # The estimated exchange timestamp calculated by the strategy is local_ts + latency (1015 + (1015 - 1000) = 1030)
    # So the order is queued to execute at 1030. Let's tick() past that.
    gateway._engine.tick(1035)

    print("\n--- INTERNAL EXCHANGE STATE (ORDER BOOK CHECK) ---")
    # For debugging only: inspect the engine's internal order book
    print(f"Resting orders: {gateway._engine.book.orders}")

    print("\n[run_sim] Simulation complete.")

if __name__ == "__main__":
    run_simulation()