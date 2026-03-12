"""
Minimal Integration Harness
Orchestrates the interaction between the Strategy and Exchange modules.
"""
import os
import sys

# Add the project root to the Python path so we can import project modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from strategy.noop import NoOpStrategy
from exchange import ExchangeGateway, Order, Side, TimeInForce
from exchange.models import QUANTITY_MULTIPLIER


class StrategyExchangeAdapter:
    """
    Thin adapter that exposes a `receive_order` method so that
    `BaseStrategy.submit_order` can talk to the real `ExchangeGateway`.
    """

    def __init__(self, gateway: ExchangeGateway) -> None:
        self._gateway = gateway

    def receive_order(self, price, side, amount, order_type, estimated_exchange_ts):
        """
        Translate the strategy-level order into the engine's `Order` model
        and forward it to the real exchange gateway.
        """
        side_enum = Side.BUY if str(side).lower() == "buy" else Side.SELL

        order_type_lower = str(order_type).lower()
        if order_type_lower == "ioc":
            tif = TimeInForce.IOC
        elif order_type_lower == "post_only":
            tif = TimeInForce.POST_ONLY
        else:
            tif = TimeInForce.GTC

        quantity = int(amount * QUANTITY_MULTIPLIER)

        print(
            "[run_sim] Adapter received order from strategy "
            f"(price={price}, side={side_enum}, amount={amount}, "
            f"tif={tif}, estimated_exchange_ts={estimated_exchange_ts})"
        )

        order = Order(
            order_id="sim_order_1",
            side=side_enum,
            price=price,
            quantity=quantity,
            tif=tif,
        )
        return self._gateway.submit_order(order)


def run_simulation() -> None:
    """
    Run a minimal end-to-end simulation that wires:
    - ExchangeGateway (exchange side)
    - NoOpStrategy (strategy side)
    and exercises the latency math + order submission path.
    """
    print("[run_sim] Initializing exchange gateway...")
    gateway = ExchangeGateway()

    print("[run_sim] Initializing NoOpStrategy...")
    strategy = NoOpStrategy()

    print("[run_sim] Connecting strategy to exchange via adapter...")
    adapter = StrategyExchangeAdapter(gateway)
    strategy.connect_exchange(adapter)

    # Simulate a market data update and test the 15ms latency calculation.
    exch_ts = 1000
    local_ts = 1015
    print(f"[run_sim] Updating strategy clock with exch_ts={exch_ts}, local_ts={local_ts}...")
    strategy.update_clock(exch_ts=exch_ts, local_ts=local_ts)
    print(f"[run_sim] Current latency estimate: {strategy.current_latency_estimate} (expected 15)")

    print("[run_sim] Submitting a sample buy order through the strategy...")
    result = strategy.submit_order(price=70_000, side="buy", amount=1, order_type="GTC")

    print("[run_sim] Exchange returned result:")
    print(f"  accepted={result.accepted}")
    print(f"  reason={result.reason}")
    print(f"  num_fills={len(result.fills)}")

    # Inspect whether our simulated order is still resting on the book
    resting = gateway.get_order("sim_order_1")
    print(f"[run_sim] Resting order lookup for 'sim_order_1': {resting}")

    print("[run_sim] Simulation complete.")


if __name__ == "__main__":
    run_simulation()

