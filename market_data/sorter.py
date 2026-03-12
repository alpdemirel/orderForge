from typing import List, Tuple
from common.types import MarketEvent

def _get_sort_key(event: MarketEvent) -> Tuple[int, int, str]:
    """
    Extracts the deterministic sort key for a market event.

    For the new Bitfinex architecture, dual timestamps are highly prioritized:
    Priority:
    1. exchange_ts_ns (Time stamped by Exchange, if available > 0)
    2. local_ts_ns (Time of capture by EC2)
    3. channel (Lexicographical fallback)
    """
    primary_ts = event.exchange_ts_ns if event.exchange_ts_ns > 0 else event.local_ts_ns
    return (primary_ts, event.local_ts_ns, event.channel)

def sort_market_events(events: List[MarketEvent]) -> List[MarketEvent]:
    """
    Sorts a list of MarketEvents deterministically based on exchange timestamp, 
    local capture timestamp, and channel name.
    """
    return sorted(events, key=_get_sort_key)
