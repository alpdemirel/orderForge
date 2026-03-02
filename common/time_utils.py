"""Time helpers shared across modules."""

from time import time_ns


def now_local_ns() -> int:
    """Return local wall-clock timestamp in nanoseconds since Unix epoch."""
    return time_ns()
