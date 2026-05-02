"""Strategy module — importing this package registers all strategies."""

# Importing each strategy module triggers its register(...) call.
from strategy import avellaneda_stoikov  # noqa: F401
from strategy import glft  # noqa: F401
from strategy import glft_microprice  # noqa: F401
from strategy import fixed_spread  # noqa: F401
