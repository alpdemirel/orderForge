# Avellaneda-Stoikov Market Making

Implementation of the 2008 Avellaneda & Stoikov strategy. It maximizes terminal wealth while managing inventory risk by dynamically skewing quotes rather than placing them symmetrically around the mid-price.

([Read the original paper here](../references/avellaneda-stoikov-2008.pdf))

### 1. Reservation Price ($r$)
The inventory-adjusted mid-price. The model lowers the price if you are long (to sell) and raises it if you are short (to buy).

$$r(s, t, q) = s - q \gamma \sigma^2 (T - t)$$

* **$s$**: Market mid-price
* **$q$**: Inventory position
* **$\gamma$**: Risk aversion parameter
* **$\sigma$**: Market volatility
* **$T - t$**: Time remaining (or `1` for continuous)

### 2. Optimal Half-Spread ($\delta$)
The optimal distance from the reservation price to place quotes. The spread widens with higher volatility ($\sigma$) or higher risk aversion ($\gamma$).

$$\delta = \frac{\gamma \sigma^2 (T - t)}{2} + \frac{1}{\gamma} \ln \left( 1 + \frac{\gamma}{k} \right)$$

* **$k$**: Order book liquidity (density) parameter

### 3. Final Quotes submitted to the Exchange
* **Bid Quote:** $r - \delta$
* **Ask Quote:** $r + \delta$