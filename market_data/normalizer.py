import logging
from typing import List, Optional
from common.types import MarketEvent
from common.events import NormalizedEvent, OrderBookSnapshot, OrderUpdate

logger = logging.getLogger(__name__)

def normalize_event(raw_event: MarketEvent) -> List[NormalizedEvent]:
    """
    Takes a raw MarketEvent and returns a list of NormalizedEvent objects.
    A single snapshot message can become one OrderBookSnapshot.
    A single l2update message with multiple changes will become multiple OrderUpdates.
    """
    events: List[NormalizedEvent] = []
    
    # Safe guard
    if not isinstance(raw_event.raw_json, dict):
        return events
        
    msg_type = raw_event.raw_json.get("type")
    product_id = raw_event.raw_json.get("product_id", "UNKNOWN")
    
    if msg_type == "snapshot":
        bids = raw_event.raw_json.get("bids", [])
        asks = raw_event.raw_json.get("asks", [])
        events.append(OrderBookSnapshot(
            local_ts_ns=raw_event.local_ts_ns,
            product_id=product_id,
            bids=bids,
            asks=asks
        ))
        
    elif msg_type == "l2update":
        changes = raw_event.raw_json.get("changes", [])
        for change in changes:
            # Coinbase format: ["buy", "price", "size"]
            if len(change) >= 3:
                side = change[0]
                price = change[1]
                size = change[2]
                events.append(OrderUpdate(
                    local_ts_ns=raw_event.local_ts_ns,
                    product_id=product_id,
                    side=side,
                    price=price,
                    size=size
                ))
    else:
        # Ignore heartbeat, subscriptions, received, open, done, match for L2
        pass
        
    return events
