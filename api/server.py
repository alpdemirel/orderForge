"""
FastAPI backend for OrderForge Backtest UI.

Endpoints:
  GET  /api/strategies          — available strategies + parameter metadata
  GET  /api/data-days           — available capture days in data/
  POST /api/backtest            — launch backtest job (multi-strategy, multi-day)
  GET  /api/backtest/{id}/status — SSE stream of per-strategy per-day progress
  GET  /api/backtest/{id}/results — full results once complete
"""

import asyncio
import orjson
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load secret .env file seamlessly
load_dotenv()
import queue
import shutil
import tarfile
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import boto3
from botocore.exceptions import NoCredentialsError, ClientError

from sim.analysis import run_full_analysis
import strategy  # triggers strategy registration
from strategy import registry as strategy_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    sim_executor.shutdown(wait=False, cancel_futures=True)
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="OrderForge Backtest API", lifespan=lifespan)

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

# Thread pool for coordination and IO-bound tasks (S3 downloads, accuracy analysis).
# Threads just block waiting for their process slot — keep count modest.
_cpu = os.cpu_count() or 4
executor = ThreadPoolExecutor(max_workers=_cpu * 2)
# Process pool for CPU-bound simulations — bypasses the GIL so N strategies run on N cores
sim_executor = ProcessPoolExecutor(max_workers=_cpu)


# ── Models ───────────────────────────────────────────────────────────────

class StrategyConfig(BaseModel):
    strategy_id: str
    parameters: dict[str, float] = {}
    label: str | None = None  # auto-generated from strategy name if omitted


class BacktestRequest(BaseModel):
    days: list[str]  # e.g. ["2026-04-15", "2026-03-29"]
    # New multi-strategy format
    strategies: list[StrategyConfig] | None = None
    # Legacy single-strategy fields (auto-wrapped into strategies[0] if strategies is absent)
    strategy_id: str | None = None
    parameters: dict[str, float] = {}
    # Optional account seeding. When initial_cash_usd is None the strategy
    # runs unconstrained (legacy behavior); a numeric value enables balance
    # enforcement.  Base inventory always starts at 0 — the realistic
    # "fresh account" scenario where the strategy builds inventory itself.
    initial_cash_usd: float | None = None


# ── GET /api/strategies ──────────────────────────────────────────────────
#
# Convention for new strategies:
# Strategies that quote in BTC should declare an "order_size" parameter
# representing the per-quote clip in BTC.  The frontend EffectiveConfig
# panel and the engine's balance auto-shrink (BaseStrategy.fit_quote)
# both rely on that name.  Strategies that don't fit the convention are
# fine — the EffectiveConfig panel hides itself when "order_size" is
# absent — but adopting the convention keeps the balance UX consistent.

@app.get("/api/strategies")
def get_strategies():
    return strategy_registry.list_public_metadata()


# ── events.log resolution (handles S3-archived tarballs) ────────────────

def _find_events_log(day_dir: Path) -> Path | None:
    """Return events.log path if already on disk; no extraction."""
    direct = day_dir / "events.log"
    if direct.exists():
        return direct
    nested = day_dir / day_dir.name / "events.log"
    if nested.exists():
        return nested
    return None


def _ensure_events_log(day_dir: Path) -> Path | None:
    """Return events.log path, extracting from {DATE}.tar.gz on first use."""
    found = _find_events_log(day_dir)
    if found:
        return found
    archive = day_dir / f"{day_dir.name}.tar.gz"
    if not archive.exists():
        return None
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if member.name.endswith("/events.log") or member.name == "events.log":
                tf.extract(member, path=day_dir, filter="data")
                break
    return _find_events_log(day_dir)


# ── GET /api/data-days ───────────────────────────────────────────────────

@app.get("/api/data-days")
def get_data_days():
    days = []
    if not DATA_DIR.exists():
        return days
    for d in sorted(DATA_DIR.iterdir()):
        if not d.is_dir():
            continue
        events_file = _find_events_log(d)
        archive = d / f"{d.name}.tar.gz"
        if events_file:
            source = events_file
        elif archive.exists():
            source = archive
        else:
            continue
        size_mb = round(source.stat().st_size / (1024 * 1024), 1)
        days.append({
            "date": d.name,
            "file": str(source),
            "size_mb": size_mb,
        })
    return days


# ── POST /api/backtest ───────────────────────────────────────────────────

@app.post("/api/backtest")
async def launch_backtest(req: BacktestRequest):
    job_id = uuid.uuid4().hex[:12]

    # Validate days exist (extract from tarball on first use if needed)
    day_files = {}
    for day in req.days:
        events_file = _ensure_events_log(DATA_DIR / day)
        if not events_file:
            raise HTTPException(status_code=404, detail=f"No events.log for {day}")
        day_files[day] = str(events_file)

    # Normalize to list of strategy configs (handle legacy single-strategy format)
    if req.strategies:
        raw_strategies = req.strategies
    elif req.strategy_id:
        raw_strategies = [StrategyConfig(strategy_id=req.strategy_id, parameters=req.parameters)]
    else:
        raise HTTPException(status_code=422, detail="Must provide strategies or strategy_id")

    # Validate all strategies exist and resolve display labels
    strategy_counts: dict[str, int] = {}
    for sc in raw_strategies:
        try:
            strategy_registry.get(sc.strategy_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown strategy: {sc.strategy_id}")
        strategy_counts[sc.strategy_id] = strategy_counts.get(sc.strategy_id, 0) + 1

    same_id_seen: dict[str, int] = {}
    resolved: list[dict] = []
    for sc in raw_strategies:
        if sc.label:
            label = sc.label
        else:
            meta = strategy_registry.get(sc.strategy_id)
            name = meta["name"]
            if strategy_counts[sc.strategy_id] > 1:
                same_id_seen[sc.strategy_id] = same_id_seen.get(sc.strategy_id, 0) + 1
                label = f"{name} #{same_id_seen[sc.strategy_id]}"
            else:
                label = name
        resolved.append({"strategy_id": sc.strategy_id, "parameters": sc.parameters, "label": label})

    # Reject duplicate labels — they would silently corrupt the job store
    all_labels = [s["label"] for s in resolved]
    if len(all_labels) != len(set(all_labels)):
        raise HTTPException(status_code=422, detail="Strategy labels must be unique")

    _jobs[job_id] = {
        "strategies": resolved,
        "days": req.days,
        "day_files": day_files,
        "initial_cash_usd": req.initial_cash_usd,
        "results": {s["label"]: {} for s in resolved},
        "status": {s["label"]: {day: "pending" for day in req.days} for s in resolved},
        "errors": {s["label"]: {} for s in resolved},
        "done_event": threading.Event(),
        "updates": queue.Queue(),
        "completion_lock": threading.Lock(),  # guards the all-done check
    }

    # Launch one thread per (strategy, day) pair
    for sc in resolved:
        for day in req.days:
            executor.submit(_run_day, job_id, sc["label"], day)

    return {"job_id": job_id}


def _simulate(capture_file: str, strategy_id: str, strategy_params: dict,
              default_latency_ms: int, initial_cash_usd: float | None) -> dict:
    """Runs in a child process — no shared state, pure computation."""
    return run_full_analysis(
        capture_file=capture_file,
        strategy_id=strategy_id,
        strategy_params=strategy_params,
        default_latency_ms=default_latency_ms,
        initial_cash_usd=initial_cash_usd,
    )


def _run_day(job_id: str, label: str, day: str):
    job = _jobs[job_id]
    job["status"][label][day] = "running"
    _push_update(job, label, day, "running")

    sc = next(s for s in job["strategies"] if s["label"] == label)
    default_latency_ms = int(sc["parameters"].get("default_latency_ms", 50))

    try:
        # Offload to a separate process so all strategies run on real CPU cores in parallel
        result = sim_executor.submit(
            _simulate,
            job["day_files"][day],
            sc["strategy_id"],
            sc["parameters"],
            default_latency_ms,
            job["initial_cash_usd"],
        ).result()
        job["results"][label][day] = result
        job["status"][label][day] = "completed"
        _push_update(job, label, day, "completed")
    except Exception as e:
        job["errors"][label][day] = str(e)
        job["status"][label][day] = "error"
        _push_update(job, label, day, "error", error=str(e))

    # Check if all (label, day) pairs are done — lock prevents double all_done emission
    with job["completion_lock"]:
        all_done = all(
            s in ("completed", "error")
            for label_statuses in job["status"].values()
            for s in label_statuses.values()
        )
        if all_done and not job["done_event"].is_set():
            _push_update(job, "__system__", "__all__", "all_done")
            job["done_event"].set()


def _push_update(job: dict, label: str, day: str, status: str, error: str = ""):
    job["updates"].put_nowait({"label": label, "day": day, "status": status, "error": error})


# ── GET /api/backtest/{job_id}/status (SSE) ──────────────────────────────

@app.get("/api/backtest/{job_id}/status")
async def backtest_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        job = _jobs[job_id]

        # Replay current status for any (label, day) pairs that already started/finished
        # before the SSE connection was established (race condition fix)
        for lbl, label_statuses in job["status"].items():
            for day, status in label_statuses.items():
                if status != "pending":
                    error = job["errors"].get(lbl, {}).get(day, "")
                    yield f"data: {orjson.dumps({'label': lbl, 'day': day, 'status': status, 'error': error}).decode()}\n\n"

        # If already all done, send terminal event and stop
        if job["done_event"].is_set():
            yield f"data: {orjson.dumps({'label': '__system__', 'day': '__all__', 'status': 'all_done', 'error': ''}).decode()}\n\n"
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

            yield f"data: {orjson.dumps(update).decode()}\n\n"

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
        "strategies": job["results"],   # {label: {day: result_dict}}
        "errors": job["errors"],         # {label: {day: error_str}}
        "status": job["status"],         # {label: {day: status}}
        "strategy_labels": [s["label"] for s in job["strategies"]],  # ordered list
    }


# ── GET /api/backtest/{job_id}/accuracy ──────────────────────────────────
#
# Runs the fill-accuracy diagnostic (same logic as scripts/diagnose_fill_accuracy.py)
# lazily on first request, then caches the result in the job store.

@app.get("/api/backtest/{job_id}/accuracy")
async def backtest_accuracy(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = _jobs[job_id]

    # Wait for the normal backtest to finish first
    done = await asyncio.get_event_loop().run_in_executor(
        None, lambda: job["done_event"].wait(timeout=600.0)
    )
    if not done:
        raise HTTPException(status_code=408, detail="Backtest timed out")

    # Return cached result if available
    if "accuracy" in job:
        return {"days": job["accuracy"]}

    # Run accuracy analysis in a thread (takes several minutes per day)
    def _run_accuracy():
        import sys as _sys
        import os as _os
        scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from scripts.diagnose_fill_accuracy import run_accuracy_analysis

        accuracy = {}
        for day, events_file in job["day_files"].items():
            try:
                accuracy[day] = run_accuracy_analysis(events_file)
            except Exception as e:
                accuracy[day] = {"error": str(e)}
        job["accuracy"] = accuracy
        return accuracy

    accuracy = await asyncio.get_event_loop().run_in_executor(executor, _run_accuracy)
    return {"days": accuracy}



S3_BUCKET = "orderforge-s3-bucket"
_downloads: dict[str, dict[str, Any]] = {}

@app.get("/api/s3-days")
def get_s3_days():
    # Identify local days
    local_days = set()
    if DATA_DIR.exists():
        for d in DATA_DIR.iterdir():
            if d.is_dir() and (_find_events_log(d) or (d / f"{d.name}.tar.gz").exists()):
                local_days.add(d.name)
    
    # Identify remote days
    s3_days = []
    try:
        s3 = boto3.client("s3")
        res = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="bitfinex/btcusd/", Delimiter="/")
        for prefix in res.get("CommonPrefixes", []):
            # prefix format: bitfinex/btcusd/YYYY-MM-DD/
            date_str = prefix["Prefix"].split("/")[-2]
            
            status = "remote"
            if date_str in local_days:
                status = "local"
            elif date_str in _downloads and _downloads[date_str].get("status") == "downloading":
                status = "downloading"
                
            s3_days.append({"date": date_str, "status": status})
            
    except (NoCredentialsError, ClientError) as e:
        # Fallback to just local days if S3 access fails
        print(f"S3 Listing Failed: {e}")
        for ld in local_days:
            s3_days.append({"date": ld, "status": "local"})
            
    return {"days": sorted(s3_days, key=lambda x: x["date"], reverse=True)}


class S3DownloadRequest(BaseModel):
    day: str

@app.post("/api/s3-download")
def download_s3_data(req: S3DownloadRequest):
    day = req.day
    if day in _downloads and _downloads[day]["status"] == "downloading":
        return {"status": "already downloading"}
        
    # Set up download state
    _downloads[day] = {"status": "downloading", "progress": 0, "error": None}
    
    # Fire off background thread
    executor.submit(_s3_downloadTask, day)
    return {"status": "started"}

def _s3_downloadTask(day: str):
    target_dir = DATA_DIR / day
    target_dir.mkdir(parents=True, exist_ok=True)
    tar_path = target_dir / f"{day}.tar.gz"
    
    try:
        s3 = boto3.client("s3")
        s3_key = f"bitfinex/btcusd/{day}/{day}.tar.gz"
        
        # Get purely for filesize progress reporting
        obj = s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
        total_size = obj["ContentLength"]
        downloaded = 0
        
        def progress_callback(bytes_amount):
            nonlocal downloaded
            downloaded += bytes_amount
            _downloads[day]["progress"] = (downloaded / total_size) * 100
        
        s3.download_file(S3_BUCKET, s3_key, str(tar_path), Callback=progress_callback)
        _downloads[day]["status"] = "completed"
        
    except Exception as e:
        _downloads[day]["status"] = "error"
        _downloads[day]["error"] = str(e)
        if tar_path.exists():
            tar_path.unlink()


@app.get("/api/s3-download/{day}/status")
async def download_status(day: str):
    async def event_stream():
        while True:
            if day not in _downloads:
                yield f"data: {orjson.dumps({'status': 'unknown'}).decode()}\n\n"
                break
                
            state = _downloads[day]
            yield f"data: {orjson.dumps(state).decode()}\n\n"
            
            if state["status"] in ("completed", "error"):
                break
                
            await asyncio.sleep(0.5)
            
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.delete("/api/data-days/{day}")
def delete_local_day(day: str):
    target_dir = DATA_DIR / day
    if target_dir.exists() and target_dir.is_dir():
        shutil.rmtree(target_dir)
        # Check if there are symlinks inside DATA_DIR that match this (from old tests)
        # Actually rmtree deletes the symlink itself if it's the target_dir, or the contents if it's a dir.
        return {"status": "deleted"}
    return {"status": "not found"}

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
