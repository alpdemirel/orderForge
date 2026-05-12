"""
Strategy registry — single source of truth for strategy metadata.

Each strategy registers itself at import time via `register(...)`. The API,
analysis layer, and CLI all read from this registry instead of hardcoding
strategy IDs and parameter schemas.

Adding a new strategy:
    1. Implement the strategy class in strategy/<name>.py
    2. Call register(...) at module level in that file
    3. Import the module in strategy/__init__.py so registration runs
"""
from typing import Any

# strategy_id → metadata dict
_REGISTRY: dict[str, dict[str, Any]] = {}


def register(
    strategy_id: str,
    name: str,
    description: str,
    cls: type,
    parameters: list[dict],
    liquidity_attr: str,
    category: str = "uncategorized",
    capital_check: dict | None = None,
) -> None:
    """Register a strategy. Called at module import time by each strategy file.

    capital_check tells the UI how much cash is needed to run this strategy:
      {"mode": "clip", "multiplier": N}
          → healthy when cash ≥ N × order_size × BTC_price
      {"mode": "inventory", "param": "max_inventory"}
          → healthy when cash ≥ params["max_inventory"] × BTC_price
    """
    _REGISTRY[strategy_id] = {
        "id": strategy_id,
        "name": name,
        "description": description,
        "cls": cls,
        "parameters": parameters,
        "liquidity_attr": liquidity_attr,
        "category": category,
        "capital_check": capital_check or {"mode": "clip", "multiplier": 5},
    }


def get(strategy_id: str) -> dict[str, Any]:
    """Return the full registry entry for a strategy_id, or raise."""
    if strategy_id not in _REGISTRY:
        raise ValueError(
            f"Unknown strategy_id: '{strategy_id}'. "
            f"Registered: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[strategy_id]


def list_public_metadata() -> list[dict]:
    """Public-facing metadata (no class refs) for the API /strategies endpoint."""
    return [
        {"id": s["id"], "name": s["name"], "description": s["description"],
         "parameters": s["parameters"], "category": s["category"],
         "capital_check": s["capital_check"]}
        for s in _REGISTRY.values()
    ]


def list_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def instantiate(strategy_id: str, params: dict):
    """Instantiate a strategy, passing only parameters its constructor accepts."""
    entry = get(strategy_id)
    cls = entry["cls"]
    accepted = {p["name"] for p in entry["parameters"]}
    filtered = {k: v for k, v in params.items() if k in accepted}
    return cls(**filtered)


def get_liquidity_param(strategy_id: str, strategy_instance) -> float:
    """Read the strategy-specific liquidity parameter (e.g. k or kappa)."""
    attr = get(strategy_id)["liquidity_attr"]
    return getattr(strategy_instance, attr)
