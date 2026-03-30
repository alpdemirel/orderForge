"""
Avellaneda-Stoikov Market Making Strategy
Reference: High-frequency trading in a limit order book (Avellaneda & Stoikov, 2008)
"""
import math
import time
from collections import deque
from strategy.base import BaseStrategy
from exchange.models import Side, QUANTITY_MULTIPLIER


class AvellanedaStoikovStrategy(BaseStrategy):
    """
    Implementation of the Avellaneda-Stoikov market making model.
    Dynamically adjusts bid/ask quotes based on inventory risk and market volatility.

    The model computes quotes in two steps:
      1. Reservation price  r = s - q * gamma * sigma^2 * (T - t)
      2. Optimal half-spread delta = (gamma * sigma^2 * (T - t) / 2) + (1 / gamma) * ln(1 + gamma / k)

    Final quotes:
        bid_price = r - delta
        ask_price = r + delta
    """

    # Volatility estimator: rolling window of mid-price log-returns
    
    _VOL_WINDOW = 100   # number of mid-price samples to keep

    def __init__(
        self,
        risk_aversion: float = 0.1,
        order_size: float = 1.0,
        session_duration: float = 1.0,
    ):
        super().__init__()

        # AS model parameters
        self.gamma = risk_aversion      # Inventory risk aversion (gamma)
        self.k = 1.5                    # Order book liquidity / density parameter (kappa)

        # Volatility state
        self.sigma = 0.0                # Current volatility estimate (updated on each tick)
        self._mid_prices: deque = deque(maxlen=self._VOL_WINDOW)

        # Inventory
        self.inventory_position = 0.0
        self.order_size = order_size

        # Time tracking — used to compute (T - t) ∈ [0, 1]
        self._session_start: float = time.monotonic()
        self._session_duration: float = session_duration   # seconds (or normalised units)

    # Internal helpers
    
    def _time_left(self) -> float:
        """
        Returns T - t normalised to [0, 1].
        Clamps to a small positive floor so formulas stay well-defined near T.
        """
        elapsed = time.monotonic() - self._session_start
        remaining = 1.0 - elapsed / self._session_duration
        return max(remaining, 1e-6)

    def _update_volatility(self, mid_price: float) -> None:
        """
        Estimates sigma as the standard deviation of recent log-returns.
        Requires at least 2 samples; sigma stays 0.0 until then.
        """
        self._mid_prices.append(mid_price)
        n = len(self._mid_prices)
        if n < 2:
            return

        prices = list(self._mid_prices)
        log_returns = [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, n)
        ]
        mean = sum(log_returns) / len(log_returns)
        variance = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
        self.sigma = math.sqrt(variance)

    # Core AS formulas
    
    def calculate_reservation_price(self, mid_price: float, time_left: float) -> float:
        """
        Inventory-adjusted theoretical mid-price.
        r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
        """
        risk_adjustment = self.inventory_position * self.gamma * (self.sigma ** 2) * time_left
        return mid_price - risk_adjustment

    def calculate_optimal_half_spread(self, time_left: float) -> float:
        """
        Calculates the optimal half-spread to be applied symmetrically around the reservation price.
        delta = (gamma * sigma^2 * (T - t) / 2) + (1 / gamma) * ln(1 + gamma / k)
        """
        risk_term = (self.gamma * (self.sigma ** 2) * time_left) / 2.0
        liquidity_term = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.k)
        return risk_term + liquidity_term

    
    def calculate_quotes(self, mid_price: float) -> tuple[float, float]:
        """
        Returns (bid_price, ask_price) using the full AS model.
        """
        if self.sigma == 0.0:
            # Volatility not yet estimated — fall back to symmetric spread
            half_spread = (1.0 / self.gamma) * math.log(1.0 + self.gamma / self.k)
            return mid_price - half_spread, mid_price + half_spread

        time_left = self._time_left()
        reservation = self.calculate_reservation_price(mid_price, time_left)
        half_spread = self.calculate_optimal_half_spread(time_left)

        bid_price = reservation - half_spread
        ask_price = reservation + half_spread
        
        return bid_price, ask_price

    # Event handlers
    
    def on_market_data(self, event: dict) -> None:
        """
        Triggered when new market data arrives.
        Updates volatility estimate and recomputes/submits quotes.
        """
        mid_price = (event["best_bid"] + event["best_ask"]) / 2.0
        self._update_volatility(mid_price)

        # Don't quote until sigma is initialised
        if self.sigma == 0.0:
            return

        bid_price, ask_price = self.calculate_quotes(mid_price)

        # TODO: cancel existing orders, submit new bid/ask limit orders
        # Example (adjust to your exchange API):
        #   self.cancel_all_orders()
        #   self.submit_limit_order(Side.BUY,  bid_price, self.order_size)
        #   self.submit_limit_order(Side.SELL, ask_price, self.order_size)

    def on_order_fill(self, fill_event) -> None:
        """
        Triggered when an order is executed.
        Updates inventory based on which side was filled.
        """
        filled_amount = fill_event.quantity / QUANTITY_MULTIPLIER

        if fill_event.maker_owner_id == "default":
            side = fill_event.maker_side
        elif fill_event.taker_owner_id == "default":
            side = fill_event.taker_side
        else:
            return  # not our order

        if side == Side.BUY:
            self.inventory_position += filled_amount
        else:
            self.inventory_position -= filled_amount