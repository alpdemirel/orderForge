from typing import List, Tuple, Any
from common.types import MarketEvent

def _get_sort_key(event: MarketEvent) -> Tuple[int, int, str]:
    """
    Extracts the deterministic sort key for a market event.
    
    Priority:
    1. local_ts_ns (Time of capture)
    2. sequence (Exchange sequence number if available, else 0)
    3. channel (Lexicographical fallback)
    """
    sequence = 0
    # Try to extract sequence from various common fields in raw_json
    if isinstance(event.raw_json, dict):
        sequence = event.raw_json.get("sequence", 0)
        
    return (event.local_ts_ns, sequence, event.channel)

def sort_market_events(events: List[MarketEvent]) -> List[MarketEvent]:
    """
    Sorts a list of MarketEvents deterministically based on capture timestamp, 
    exchange sequence, and channel name.
    """
    return sorted(events, key=_get_sort_key)
