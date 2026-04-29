"""
GLFT + Microprice Market Making Strategy
Reference: The micro-price: a high-frequency estimator of future prices (Stoikov, 2018)

Extends GLFT by replacing the naive mid-price with the order-book microprice.
The microprice weights best bid and ask by the opposite side's quantity,
capturing order book imbalance as a short-term price signal.

    imbalance   = bid_qty / (bid_qty + ask_qty)
    microprice  = best_ask * imbalance + best_bid * (1 - imbalance)

When bid_qty >> ask_qty → imbalance → 1 → microprice ≈ best_ask (buying pressure)
When ask_qty >> bid_qty → imbalance → 0 → microprice ≈ best_bid (selling pressure)
"""
import uuid

from strategy.glft import GLFTStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


class GLFTMicropriceStrategy(GLFTStrategy):
    """
    GLFT with microprice as the reference price instead of mid-price.
    All spread/inventory logic is inherited unchanged from GLFTStrategy.
    """

    @staticmethod
    def _compute_microprice(event: dict) -> float:
        """
        Compute microprice from top-of-book sizes.
        Falls back to mid-price if depth data is missing or sizes are zero.
        """
        bids = event.get("depth_bids", [])
        asks = event.get("depth_asks", [])

        if bids and asks:
            bid_qty = bids[0][1]
            ask_qty = asks[0][1]
            total = bid_qty + ask_qty
            if total > 0:
                imbalance = bid_qty / total
                return event["best_ask"] * imbalance + event["best_bid"] * (1 - imbalance)

        return (event["best_bid"] + event["best_ask"]) / 2.0

    def on_market_data(self, event: dict) -> None:
        if self._session_start_ms is None:
            self._session_start_ms = event["local_ts_ms"]

        microprice = self._compute_microprice(event)

        if self.sigma != 0.0:
            bid_price, ask_price = self.calculate_quotes(microprice)
        else:
            bid_price = ask_price = None

        self._update_volatility(microprice, event["local_ts_ms"])

        if bid_price is None and ask_price is None:
            return

        self._cancel_active_orders()
        new_ids = []

        if bid_price is not None:
            bid_id = f"glft_mp_bid_{uuid.uuid4().hex[:8]}"
            self.submit_order(bid_price, "buy", self.order_size, order_id=bid_id)
            new_ids.append(bid_id)

        if ask_price is not None:
            ask_id = f"glft_mp_ask_{uuid.uuid4().hex[:8]}"
            self.submit_order(ask_price, "sell", self.order_size, order_id=ask_id)
            new_ids.append(ask_id)

        self._active_order_ids = new_ids


# ── Registry registration ────────────────────────────────────────────────
from strategy.registry import register

register(
    strategy_id="glft_microprice",
    name="GLFT + Microprice",
    description="Inventory-aware market making using order book imbalance as a price signal via microprice instead of mid-price. (Stoikov, 2018)",
    cls=GLFTMicropriceStrategy,
    parameters=[
        {"name": "risk_aversion", "label": "Risk Aversion (gamma)", "type": "float",
         "default": 0.001, "min": 0.0001, "max": 1.0, "step": 0.0001,
         "description": "Controls how aggressively the strategy skews quotes to reduce inventory."},
        {"name": "order_size", "label": "Order Size (BTC)", "type": "float",
         "default": 0.01, "min": 0.001, "max": 1.0, "step": 0.001,
         "description": "Size of each bid/ask quote in BTC."},
        {"name": "session_duration", "label": "Session Duration (s)", "type": "float",
         "default": 86400, "min": 3600, "max": 86400, "step": 3600,
         "description": "Trading session length in seconds."},
        {"name": "max_inventory", "label": "Max Inventory (BTC)", "type": "float",
         "default": 0.5, "min": 0.01, "max": 10.0, "step": 0.01,
         "description": "Hard inventory limit. Strategy stops quoting one side when this is reached."},
        {"name": "kappa", "label": "Kappa", "type": "float",
         "default": 1.5, "min": 0.1, "max": 10.0, "step": 0.1,
         "description": "Order book liquidity/density parameter."},
    ],
    liquidity_attr="kappa",
    category="Stochastic Control",
)
