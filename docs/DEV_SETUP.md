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

## Run capture stub
After setup:

```bash
python -m capture.main
```

Optional output path:

```bash
python -m capture.main ./data/sample_capture.log
```
