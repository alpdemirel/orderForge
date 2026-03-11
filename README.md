# OrderForge

OrderForge is a Python-first monorepo scaffold for a high-fidelity exchange backtest simulator
focused on Coinbase BTC-USD market data.

## Repository layout
- `capture/` - capture pipeline stub executable module
- `market_data/` - replay engine module placeholder
- `exchange/` - exchange simulator module placeholder
- `strategy/` - strategy module placeholder
- `common/` - shared utilities and types
- `tests/` - pytest test suite
- `docs/` - architecture, setup, and roadmap
- `scripts/` - helper scripts (reserved)

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Run tests
```bash
pytest
```

## Run capture stub
Default output file:
```bash
python -m capture.main
```

Custom output file:
```bash
python -m capture.main ./capture_stub_output.log
```

The stub writes sample lines in the required format:

`<local_ts_ns>|<channel>|<raw_json>`

## Week 1 docs
- `docs/ARCHITECTURE.md`
- `docs/DEV_SETUP.md`
- `docs/PHASE1_PLAN.md`
- `docs/PROJECT_GUIDE.md` (detailed team walkthrough)
- `docs/WORK_PARTITION.md` (4-person detailed responsibilities and sequence)
- `docs/DATA_RETRIEVAL.md` (how to pull captured data from S3 or EC2 for replay)
- `docs/L3_VERIFY_AND_DEPLOY.md` (verify L3 format, deploy to EC2, test S3)
