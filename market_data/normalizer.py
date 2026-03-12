import logging
from typing import List, Optional
from common.types import MarketEvent
from common.events import NormalizedEvent, OrderUpdate, OrderCancel
from market_data.reader import (
    is_bitfinex_snapshot, 
    is_bitfinex_update, 
    extract_bitfinex_snapshot_orders, 
    extract_bitfinex_update
)

logger = logging.getLogger(__name__)

def normalize_event(raw_event: MarketEvent, product_id: str = "BTC-USD") -> List[NormalizedEvent]:
    """
    Takes a raw Bitfinex dual-timestamp MarketEvent and returns a list of NormalizedEvent objects.
    A single bf_snapshot message becomes multiple OrderUpdates.
    A single bf_update message becomes a single OrderUpdate or OrderCancel.
    """
    events: List[NormalizedEvent] = []
    
    # Priority timestamp
    exchange_ts = raw_event.exchange_ts_ns
    local_ts = raw_event.local_ts_ns
    
    if is_bitfinex_snapshot(raw_event):
        orders = extract_bitfinex_snapshot_orders(raw_event)
        for order in orders:
            events.append(OrderUpdate(
                exchange_ts_ns=exchange_ts,
                local_ts_ns=local_ts,
                product_id=product_id,
                order_id=order["order_id"],
                side=order["side"],
                price=order["price"],
                amount=order["amount"]
            ))
            
    elif is_bitfinex_update(raw_event):
        update = extract_bitfinex_update(raw_event)
        if update:
            if update["removed"]:
                events.append(OrderCancel(
                    exchange_ts_ns=exchange_ts,
                    local_ts_ns=local_ts,
                    product_id=product_id,
                    order_id=update["order_id"]
                ))
            else:
                events.append(OrderUpdate(
                    exchange_ts_ns=exchange_ts,
                    local_ts_ns=local_ts,
                    product_id=product_id,
                    order_id=update["order_id"],
                    side=update["side"],  # type: ignore
                    price=update["price"],
                    amount=update["amount"]
                ))
    else:
        # Ignore subscribed, info
        pass
        
    return events
