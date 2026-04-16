# Developer Setup

## Prerequisites
- Python 3.10+
- Git

## Linux (Ubuntu)
Install dependencies:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Create a virtual environment and install project dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]
```

## macOS
Install dependencies with Homebrew:

```bash
brew install python
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]
```

## Windows
Recommended:
- WSL2 (Ubuntu) with the Linux instructions above, or
- Native Python 3.10+ with PowerShell.

PowerShell setup:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
```

## Run tests
```bash
pytest
```

## Run capture (local test)

Connects to Bitfinex and writes L3 data to `./data/YYYY-MM-DD/events.log`:

```bash
python -m capture.main ./data
```

For production (EC2), set the S3 bucket:
```bash
export S3_BUCKET="orderforge-s3-bucket"
python -m capture.main ./data
```

## Run a backtest (CLI)

```bash
python -m sim.main data/2026-04-15/events.log --risk-aversion 0.001 --order-size 0.01
```

## Run the Backtest UI

Requires Node.js 18+ (for the one-time frontend build).

```bash
pip install fastapi "uvicorn[standard]"
python main.py
```

Open http://localhost:8000 in your browser. On first run it will automatically install frontend dependencies and build — subsequent launches are instant.

The UI lets you select a strategy, pick one or more data days, configure parameters, and view per-day results with charts and statistics.
