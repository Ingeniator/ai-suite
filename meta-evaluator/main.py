"""
meta-evaluator — CEFS orchestration + evaluator quality measurement.

Bridges dataset-mock (scored traces) → annotator-mock (human labels)
and computes evaluator agreement metrics (accuracy, precision, recall, Cohen's κ).

Port: 8020
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel

from orchestrator import execute_run
from store import Run, store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("meta-evaluator")

ROOT_PATH          = os.getenv("ROOT_PATH", "")
DATASET_MOCK_URL   = os.getenv("DATASET_MOCK_URL",  "http://dataset-mock:9100")
ANNOTATOR_MOCK_URL = os.getenv("ANNOTATOR_MOCK_URL","http://annotator-mock:8010")
ANNOTATION_OVERLAP = int(os.getenv("ANNOTATION_OVERLAP", "3"))
POLL_INTERVAL_S    = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))


# ── Prometheus metrics ────────────────────────────────────────────────────────

runs_total  = Counter("cefs_runs_total",    "CEFS runs by terminal state", ["state"])
agreement_g = Gauge("cefs_agreement_rate",  "Evaluator agreement rate from last completed run")
accuracy_g  = Gauge("cefs_accuracy",        "Evaluator accuracy from last completed run")
kappa_g     = Gauge("cefs_cohens_kappa",    "Cohen's κ from last completed run")
items_g     = Gauge("cefs_items_total",     "Items annotated in last completed run")


def _on_run_done(run: Run) -> None:
    """Called via asyncio task add_done_callback — update Prometheus gauges."""
    runs_total.labels(state=run.state).inc()
    if run.state == "DONE" and run.metrics:
        agreement_g.set(run.metrics.get("agreement_rate", 0))
        accuracy_g.set(run.metrics.get("accuracy", 0))
        kappa_g.set(run.metrics.get("cohens_kappa", 0))
        items_g.set(run.metrics.get("total", 0))


def _spawn_run(run: Run) -> None:
    """Create and register an asyncio task for a run."""
    task = asyncio.create_task(
        execute_run(run, DATASET_MOCK_URL, ANNOTATOR_MOCK_URL, ANNOTATION_OVERLAP)
    )
    run._bg_task = task
    task.add_done_callback(lambda _t, r=run: _on_run_done(r))


# ── Auto-poller ───────────────────────────────────────────────────────────────

async def _auto_poll() -> None:
    """
    Every POLL_INTERVAL_S seconds: query dataset-mock for new datasets.
    Any dataset not yet in store.seen_datasets gets a new run spawned automatically.
    """
    logger.info(
        "auto-poller started — interval %ds, watching %s",
        POLL_INTERVAL_S, DATASET_MOCK_URL,
    )
    async with httpx.AsyncClient(timeout=10) as http:
        while True:
            await asyncio.sleep(POLL_INTERVAL_S)
            try:
                r = await http.get(f"{DATASET_MOCK_URL}/_mock/datasets")
                if r.status_code != 200:
                    continue
                for ds in r.json().get("datasets", []):
                    ds_id = ds["id"]
                    if ds_id not in store.seen_datasets:
                        store.seen_datasets.add(ds_id)
                        run = store.new_run(ds_id, ds.get("name", ds_id[:16]))
                        logger.info(
                            "auto-poller: new dataset %s (%s) → run %s",
                            ds_id, run.dataset_name, run.id,
                        )
                        _spawn_run(run)
            except Exception as exc:
                logger.warning("auto-poller error: %s", exc)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    poller = asyncio.create_task(_auto_poll())
    logger.info("meta-evaluator started")
    yield
    poller.cancel()
    for run in store.runs.values():
        if run._bg_task and not run._bg_task.done():
            run._bg_task.cancel()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CEFS Meta-Evaluator",
    description=(
        "Orchestrates the CEFS annotation pipeline: fetches scored traces from "
        "dataset-mock, submits them to annotator-mock for human labelling, then "
        "computes evaluator quality metrics (accuracy, precision, recall, Cohen's κ). "
        "Supports both manual run triggers and automatic polling for new datasets."
    ),
    version="1.0.0",
    lifespan=lifespan,
    root_path=ROOT_PATH,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_run(run_id: str) -> Run:
    run = store.runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail={"error": "run not found"})
    return run


# ── Schemas ───────────────────────────────────────────────────────────────────

class RunCreateRequest(BaseModel):
    dataset_id: str
    dataset_name: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "service": "meta-evaluator"}


@app.get("/metrics", tags=["meta"], response_class=PlainTextResponse)
def metrics_endpoint():
    """Prometheus scrape endpoint."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Runs ──────────────────────────────────────────────────────────────────────

@app.post("/api/v0/runs", status_code=201, tags=["RunsApi"])
async def create_run(body: RunCreateRequest):
    """
    Manually trigger a CEFS run for a specific dataset from dataset-mock.
    Returns immediately; the run executes in the background.
    Poll GET /api/v0/runs/{id} to track progress.
    """
    ds_id   = body.dataset_id
    ds_name = body.dataset_name or ds_id[:16]

    run = store.new_run(ds_id, ds_name)
    store.seen_datasets.add(ds_id)   # prevent auto-poller from double-triggering
    _spawn_run(run)

    logger.info("manual run %s created for dataset %s (%s)", run.id, ds_id, ds_name)
    return run.to_dict()


@app.get("/api/v0/runs", tags=["RunsApi"])
def list_runs(
    state:      Optional[str] = Query(default=None, description="Filter by state"),
    page:       int = 1,
    size:       int = 50,
):
    """List all runs, newest first."""
    items = list(store.runs.values())
    if state:
        items = [r for r in items if r.state == state]
    items.sort(key=lambda r: r.created_at, reverse=True)
    start = (page - 1) * size
    return {
        "items":    [r.to_dict() for r in items[start : start + size]],
        "total":    len(items),
        "has_next": start + size < len(items),
    }


@app.get("/api/v0/runs/{run_id}", tags=["RunsApi"])
def get_run(run_id: str):
    """Get run details including metrics once the run is DONE."""
    return _get_run(run_id).to_dict()


@app.post("/api/v0/runs/{run_id}/cancel", tags=["RunsApi"])
def cancel_run(run_id: str):
    """Cancel a run that is QUEUED, FETCHING, SUBMITTING, ANNOTATING, or EVALUATING."""
    run = _get_run(run_id)
    if run.state in ("DONE", "FAILED", "CANCELLED"):
        raise HTTPException(
            status_code=422, detail={"error": f"run is already {run.state}"}
        )
    if run._bg_task and not run._bg_task.done():
        run._bg_task.cancel()
    store.touch(run, "CANCELLED")
    return run.to_dict()


# ── Summary ───────────────────────────────────────────────────────────────────

@app.get("/api/v0/summary", tags=["MetricsApi"])
def summary():
    """
    Aggregate evaluator quality metrics across all completed runs.
    Returns averages for agreement rate, accuracy, F1, Cohen's κ and total item count.
    """
    done = [r for r in store.runs.values() if r.state == "DONE" and r.metrics]
    if not done:
        return {"runs": 0, "message": "no completed runs yet"}

    n = len(done)
    def avg(key: str) -> float:
        return round(sum(r.metrics.get(key, 0) for r in done) / n, 4)

    return {
        "runs":                  n,
        "total_items":           sum(r.metrics.get("total", 0) for r in done),
        "avg_agreement_rate":    avg("agreement_rate"),
        "avg_disagreement_rate": avg("disagreement_rate"),
        "avg_accuracy":          avg("accuracy"),
        "avg_precision":         avg("precision"),
        "avg_recall":            avg("recall"),
        "avg_f1":                avg("f1"),
        "avg_cohens_kappa":      avg("cohens_kappa"),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8020"))
    # Pass `app` directly (not "main:app") so uvicorn doesn't reimport the module
    # and re-register prometheus metrics, which causes a duplicate-timeseries error.
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
