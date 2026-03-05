class MarketDataError(Exception):
    """Base exception for market data errors."""
    pass

class MalformedRecordError(MarketDataError):
    """Raised when a capture record cannot be parsed."""
    pass
