"""
OrderForge Backtest UI — single entry point.

    python main.py

Opens the UI at http://localhost:8000
"""

import subprocess
import sys
from pathlib import Path

FRONTEND_DIR = Path(__file__).parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"


def ensure_frontend_built():
    if not DIST_DIR.exists() or not (DIST_DIR / "index.html").exists():
        print("Building frontend (first run only)...")
        if not (FRONTEND_DIR / "node_modules").exists():
            subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True)
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)
        print("Frontend built.\n")


if __name__ == "__main__":
    ensure_frontend_built()

    import uvicorn
    print("Starting OrderForge at http://localhost:8000")
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=False)
