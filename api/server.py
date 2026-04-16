"""
FastAPI backend for OrderForge Backtest UI.

Endpoints:
  GET  /api/strategies          — available strategies + parameter metadata
  GET  /api/data-days           — available capture days in data/
  POST /api/backtest            — launch backtest job (multi-day)
  GET  /api/backtest/{id}/status — SSE stream of per-day progress
  GET  /api/backtest/{id}/results — full results once complete
"""

import asyncio
import json
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sim.analysis import run_full_analysis

app = FastAPI(title="OrderForge Backtest API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static frontend (built Vite output) ─────────────────────────────────
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# In-memory job store
_jobs: dict[str, dict[str, Any]] = {}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

executor = ThreadPoolExecutor(max_workers=3)


# ── Models ───────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy_id: str
    days: list[str]  # e.g. ["2026-04-15", "2026-03-29"]
    parameters: dict[str, float] = {}


# ── GET /api/strategies ──────────────────────────────────────────────────

@app.get("/api/strategies")
def get_strategies():
    return [
        {
            "id": "avellaneda_stoikov",
            "name": "Avellaneda-Stoikov",
            "description": "Inventory-aware market making with dynamic bid/ask quotes based on volatility and risk aversion.",
            "parameters": [
                {
                    "name": "risk_aversion",
                    "label": "Risk Aversion (gamma)",
                    "type": "float",
                    "default": 0.001,
                    "min": 0.0001,
                    "max": 1.0,
                    "step": 0.0001,
                    "description": "Controls how aggressively the strategy skews quotes to reduce inventory.",
                },
                {
                    "name": "order_size",
                    "label": "Order Size (BTC)",
                    "type": "float",
                    "default": 0.01,
                    "min": 0.001,
                    "max": 1.0,
                    "step": 0.001,
                    "description": "Size of each bid/ask quote in BTC.",
                },
                {
                    "name": "session_duration",
                    "label": "Session Duration (s)",
                    "type": "float",
                    "default": 86400,
                    "min": 3600,
                    "max": 86400,
                    "step": 3600,
                    "description": "Trading session length in seconds. AS model uses this for time-remaining calculation.",
                },
            ],
        }
    ]


# ── GET /api/data-days ───────────────────────────────────────────────────

@app.get("/api/data-days")
def get_data_days():
    days = []
    if not DATA_DIR.exists():
        return days
    for d in sorted(DATA_DIR.iterdir()):
        if not d.is_dir():
            continue
        events_file = d / "events.log"
        if events_file.exists():
            size_mb = round(events_file.stat().st_size / (1024 * 1024), 1)
            days.append({
                "date": d.name,
                "file": str(events_file),
                "size_mb": size_mb,
            })
    return days


# ── POST /api/backtest ───────────────────────────────────────────────────

@app.post("/api/backtest")
async def launch_backtest(req: BacktestRequest):
    job_id = uuid.uuid4().hex[:12]

    # Validate days exist
    day_files = {}
    for day in req.days:
        events_file = DATA_DIR / day / "events.log"
        if not events_file.exists():
            raise HTTPException(status_code=404, detail=f"No events.log for {day}")
        day_files[day] = str(events_file)

    params = {
        "risk_aversion": req.parameters.get("risk_aversion", 0.001),
        "order_size": req.parameters.get("order_size", 0.01),
        "session_duration": req.parameters.get("session_duration", 86400.0),
        "default_latency_ms": int(req.parameters.get("default_latency_ms", 50)),
    }

    # Thread-safe queue and event for cross-thread communication
    _jobs[job_id] = {
        "days": req.days,
        "day_files": day_files,
        "params": params,
        "status": {day: "pending" for day in req.days},
        "results": {},
        "errors": {},
        "done_event": threading.Event(),
        "updates": queue.Queue(),  # thread-safe stdlib queue
    }

    # Launch backtests in background threads
    for day in req.days:
        executor.submit(_run_day, job_id, day)

    return {"job_id": job_id}


def _run_day(job_id: str, day: str):
    job = _jobs[job_id]
    job["status"][day] = "running"
    _push_update(job, day, "running")

    try:
        result = run_full_analysis(
            capture_file=job["day_files"][day],
            **job["params"],
        )
        job["results"][day] = result
        job["status"][day] = "completed"
        _push_update(job, day, "completed")
    except Exception as e:
        job["errors"][day] = str(e)
        job["status"][day] = "error"
        _push_update(job, day, "error", error=str(e))

    # Check if all days done
    if all(s in ("completed", "error") for s in job["status"].values()):
        _push_update(job, "__all__", "all_done")
        job["done_event"].set()


def _push_update(job: dict, day: str, status: str, error: str = ""):
    job["updates"].put_nowait({"day": day, "status": status, "error": error})


# ── GET /api/backtest/{job_id}/status (SSE) ──────────────────────────────

@app.get("/api/backtest/{job_id}/status")
async def backtest_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        job = _jobs[job_id]

        # Replay current status for any days that already started/finished
        # before the SSE connection was established (race condition fix)
        for day, status in job["status"].items():
            if status != "pending":
                yield f"data: {json.dumps({'day': day, 'status': status, 'error': job['errors'].get(day, '')})}\n\n"

        # If already all done, send terminal event and stop
        if job["done_event"].is_set():
            yield f"data: {json.dumps({'day': '__all__', 'status': 'all_done', 'error': ''})}\n\n"
            return

        # Stream live updates from the thread-safe queue
        while True:
            try:
                # Poll the thread-safe queue from the async loop
                update = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: job["updates"].get(timeout=30.0)
                )
            except queue.Empty:
                yield f"data: {{}}\n\n"  # keepalive
                continue

            yield f"data: {json.dumps(update)}\n\n"

            if update.get("status") == "all_done":
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── GET /api/backtest/{job_id}/results ───────────────────────────────────

@app.get("/api/backtest/{job_id}/results")
async def backtest_results(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = _jobs[job_id]

    # Wait up to 10 minutes for completion (poll threading.Event from async)
    done = await asyncio.get_event_loop().run_in_executor(
        None, lambda: job["done_event"].wait(timeout=600.0)
    )
    if not done:
        raise HTTPException(status_code=408, detail="Backtest timed out")

    return {
        "days": job["results"],
        "errors": job["errors"],
        "status": job["status"],
    }


# ── Static frontend serving (must be AFTER API routes) ──────────────────

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve static files or fall back to index.html for SPA routing."""
        file_path = FRONTEND_DIR / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
