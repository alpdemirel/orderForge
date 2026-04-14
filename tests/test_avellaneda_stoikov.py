import math
from unittest.mock import patch
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy

def test_symmetric_fallback_when_no_volatility():
    # If sigma is 0 (not enough data yet), it should quote symmetrically around mid_price
    # Use maker_fee_bps=0 to isolate the pure AS formula
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, maker_fee_bps=0)
    strategy.k = 1.5
    strategy.sigma = 0.0  # Explicitly 0

    mid_price = 100.0
    bid, ask = strategy.calculate_quotes(mid_price)

    # Expected half-spread = (1 / 0.1) * ln(1 + 0.1 / 1.5) = 10 * ln(1.0666...) ≈ 0.645
    expected_half_spread = 10.0 * math.log(1.0 + 0.1 / 1.5)

    assert math.isclose(bid, mid_price - expected_half_spread, rel_tol=1e-5)
    assert math.isclose(ask, mid_price + expected_half_spread, rel_tol=1e-5)

def test_reservation_price_shifts_with_inventory():
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.1)
    strategy.sigma = 2.0
    mid_price = 100.0
    time_left = 1.0

    # 1. Neutral Inventory (q = 0)
    strategy.inventory_position = 0.0
    r_neutral = strategy.calculate_reservation_price(mid_price, time_left)
    assert r_neutral == 100.0  # Should equal mid_price

    # 2. Long Inventory (q = 10) -> Should lower the reservation price to sell
    strategy.inventory_position = 10.0
    r_long = strategy.calculate_reservation_price(mid_price, time_left)
    assert r_long < 100.0
    # Formula: 100 - (10 * 0.1 * 4.0 * 1.0) = 100 - 4 = 96.0
    assert r_long == 96.0

    # 3. Short Inventory (q = -10) -> Should raise the reservation price to buy
    strategy.inventory_position = -10.0
    r_short = strategy.calculate_reservation_price(mid_price, time_left)
    assert r_short > 100.0
    # Formula: 100 - (-10 * 0.1 * 4.0 * 1.0) = 100 + 4 = 104.0
    assert r_short == 104.0

def test_optimal_half_spread_calculation():
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.1)
    strategy.k = 1.5
    strategy.sigma = 2.0
    time_left = 1.0

    half_spread = strategy.calculate_optimal_half_spread(time_left)
    
    # Formula: (gamma * sigma^2 * time_left / 2) + (1 / gamma) * ln(1 + gamma / k)
    # Risk term = (0.1 * 4.0 * 1.0) / 2 = 0.2
    # Liquidity term = 10 * ln(1 + 0.1 / 1.5) ≈ 0.645385
    # Total expected ≈ 0.845385
    expected_risk_term = 0.2
    expected_liq_term = 10.0 * math.log(1.0 + 0.1 / 1.5)
    
    assert math.isclose(half_spread, expected_risk_term + expected_liq_term, rel_tol=1e-5)

@patch("strategy.avellaneda_stoikov.AvellanedaStoikovStrategy._time_left")
def test_full_quotes_generation(mock_time_left):
    mock_time_left.return_value = 1.0  # Force time_left to be 1.0 for predictable math
    
    strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, maker_fee_bps=0)
    strategy.k = 1.5
    strategy.sigma = 2.0
    strategy.inventory_position = 5.0 # Long position

    mid_price = 100.0
    bid, ask = strategy.calculate_quotes(mid_price)

    # Reservation price = 100 - (5 * 0.1 * 4.0 * 1.0) = 98.0
    # Half spread = 0.2 + ~0.645 = ~0.845
    # Expected Bid = 98.0 - 0.845 = 97.155
    # Expected Ask = 98.0 + 0.845 = 98.845

    reservation_price = 98.0
    expected_half_spread = 0.2 + (10.0 * math.log(1.0 + 0.1 / 1.5))

    assert math.isclose(bid, reservation_price - expected_half_spread, rel_tol=1e-5)
    assert math.isclose(ask, reservation_price + expected_half_spread, rel_tol=1e-5)