# Exchange Decisions Log

This log captures exchange-engine assumptions and choices so they can be explained and reviewed
throughout the semester.

## Current baseline decisions

- Matching uses price-time priority with deterministic behavior.
- Supported time-in-force in baseline:
  - `GTC`
  - `IOC`
  - `POST_ONLY`
- `POST_ONLY` orders are rejected if they would execute immediately.
- `IOC` orders are canceled if any remainder is unfilled after immediate matching.

## Accounting hooks (baseline)

- Account state tracks:
  - `cash_balance`
  - `base_position`
  - `fees_paid`
- Engine tracks aggregate `fee_pool`.
- Fee model currently uses:
  - maker fee: `0.0` bps
  - taker fee: `5.0` bps

These values are placeholders and can be updated later without changing exchange interfaces.

## Known limitations

- No full portfolio/risk checks yet.
- No advanced order types beyond current baseline set.
- No exchange-specific fee tiers yet.
