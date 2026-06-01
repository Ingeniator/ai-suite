"""
CEFS run executor.

execute_run() drives a single run through its full lifecycle:

  QUEUED
    └─▶ FETCHING    download dataset files from dataset-mock
    └─▶ SUBMITTING  create dataset/project/task in annotator-mock + start
    └─▶ ANNOTATING  poll task state until DONE
    └─▶ EVALUATING  fetch assignments, compute agreement metrics
    └─▶ DONE
              ╰─▶ FAILED    on any unexpected error
              ╰─▶ CANCELLED on asyncio.CancelledError
"""

import asyncio
import io
import json
import logging
from typing import Optional

import httpx

from store import Run, store
from evaluator import compute_metrics

logger = logging.getLogger("meta-evaluator.orchestrator")

# Hard-coded annotator pool that is always available in annotator-mock
CEFS_POOL_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

_POLL_INTERVAL_S = 3    # seconds between annotation state polls
_MAX_POLL_WAIT_S = 3600  # 1-hour hard timeout for annotation phase


async def execute_run(
    run: Run,
    dataset_mock_url: str,
    annotator_mock_url: str,
    overlap: int = 3,
) -> None:
    """
    Full CEFS pipeline for one dataset.
    Mutates `run` in place (store holds the reference).
    """
    dm = dataset_mock_url.rstrip("/")
    am = annotator_mock_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30) as http:
        try:
            await _pipeline(run, http, dm, am, overlap)
        except asyncio.CancelledError:
            store.touch(run, "CANCELLED")
            logger.info("run %s cancelled", run.id)
        except Exception as exc:
            run.error = str(exc)
            store.touch(run, "FAILED")
            logger.exception("run %s failed: %s", run.id, exc)


async def _pipeline(
    run: Run,
    http: httpx.AsyncClient,
    dm: str,
    am: str,
    overlap: int,
) -> None:

    # ── 1. FETCHING ─────────────────────────────────────────────────────────────
    store.touch(run, "FETCHING")
    logger.info("run %s: fetching dataset %s from dataset-mock", run.id, run.dataset_id)

    ds_r = await http.get(f"{dm}/_mock/datasets/{run.dataset_id}")
    ds_r.raise_for_status()
    ds_meta = ds_r.json()
    files = ds_meta.get("files", [])
    if not files:
        raise ValueError(f"dataset {run.dataset_id} has no files")

    # Download all files, merge into a single item list
    all_items: list[dict] = []
    for f in files:
        fid = f["id"]
        content_r = await http.get(f"{dm}/_mock/files/{fid}")
        content_r.raise_for_status()
        raw = content_r.text.strip()
        try:
            parsed = json.loads(raw)
            items = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            # try JSONL
            items = [json.loads(line) for line in raw.splitlines() if line.strip()]
        all_items.extend(items)

    if not all_items:
        raise ValueError("dataset files contain no items")

    run.items_total = len(all_items)
    store.touch(run)
    logger.info("run %s: fetched %d items across %d file(s)", run.id, len(all_items), len(files))

    # ── 2. SUBMITTING ────────────────────────────────────────────────────────────
    store.touch(run, "SUBMITTING")

    # 2a. create dataset in annotator-mock
    am_ds_r = await http.post(
        f"{am}/api/v0/datasets",
        json={"name": run.dataset_name, "access": "organization"},
    )
    am_ds_r.raise_for_status()
    am_ds_id = am_ds_r.json()["id"]
    run.am_dataset_id = am_ds_id

    # 2b. upload items as JSONL
    jsonl_bytes = "\n".join(json.dumps(item) for item in all_items).encode()
    upload_r = await http.post(
        f"{am}/api/v0/datasets/{am_ds_id}/files",
        files={"file": ("traces.jsonl", io.BytesIO(jsonl_bytes), "application/json")},
    )
    upload_r.raise_for_status()

    # 2c. create project
    proj_r = await http.post(
        f"{am}/api/v0/markup_project",
        json={"name": f"CEFS — {run.dataset_name}"},
    )
    proj_r.raise_for_status()
    am_proj_id = proj_r.json()["uid"]
    run.am_project_id = am_proj_id

    # 2d. assign hardcoded annotator pool
    pool_r = await http.post(
        f"{am}/api/v0/markup_project/{am_proj_id}/pools/{CEFS_POOL_ID}"
    )
    if pool_r.status_code not in (200, 201, 204):
        raise ValueError(f"pool assignment returned {pool_r.status_code}: {pool_r.text}")

    # 2e. create task
    task_r = await http.post(
        f"{am}/api/v0/tasks",
        json={
            "project_id": am_proj_id,
            "dataset_id": am_ds_id,
            "overlap":    overlap,
            "name":       f"run-{run.id[:8]}",
        },
    )
    task_r.raise_for_status()
    am_task_id = task_r.json()["uid"]
    run.am_task_id = am_task_id

    # 2f. start task
    start_r = await http.post(f"{am}/api/v0/tasks/{am_task_id}/start")
    start_r.raise_for_status()

    store.touch(run)
    logger.info("run %s: annotation task %s started (%d items, overlap=%d)",
                run.id, am_task_id, run.items_total, overlap)

    # ── 3. ANNOTATING ────────────────────────────────────────────────────────────
    store.touch(run, "ANNOTATING")
    waited = 0
    while waited < _MAX_POLL_WAIT_S:
        await asyncio.sleep(_POLL_INTERVAL_S)
        waited += _POLL_INTERVAL_S

        state_r = await http.get(f"{am}/api/v0/tasks/{am_task_id}/state")
        state_r.raise_for_status()
        task_state = state_r.json().get("state")

        # update progress counter
        task_r2 = await http.get(f"{am}/api/v0/tasks/{am_task_id}")
        if task_r2.status_code == 200:
            td = task_r2.json()
            run.items_done = int(td.get("items_done", 0))
            store.touch(run)

        if task_state == "DONE":
            break
        if task_state in ("STOPPED", "ARCHIVE"):
            raise ValueError(f"annotation task entered terminal state: {task_state}")
    else:
        raise TimeoutError(f"annotation did not complete within {_MAX_POLL_WAIT_S}s")

    logger.info("run %s: annotation DONE (%d/%d items)",
                run.id, run.items_done, run.items_total)

    # ── 4. EVALUATING ────────────────────────────────────────────────────────────
    store.touch(run, "EVALUATING")
    asgn_r = await http.get(
        f"{am}/api/v0/assignments?task_id={am_task_id}&size=1000"
    )
    asgn_r.raise_for_status()
    assignments = asgn_r.json().get("items", [])

    metrics = compute_metrics(assignments)
    run.metrics = metrics
    store.touch(run, "DONE")

    logger.info(
        "run %s: DONE  agreement=%.1f%%  accuracy=%s  κ=%s",
        run.id,
        metrics.get("agreement_rate", 0) * 100,
        metrics.get("accuracy", "n/a"),
        metrics.get("cohens_kappa", "n/a"),
    )
