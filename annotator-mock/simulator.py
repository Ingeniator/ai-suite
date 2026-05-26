"""
Annotation simulator — runs as a background asyncio coroutine per task.

When a task is started, the simulator:
  1. Collects all items linked to the task's dataset.
  2. For each annotator (up to task.overlap), creates an assignment with
     simulated Agree / Disagree / Partial labels.
  3. Advances task.state → DONE when all work is done.

Label distribution (configurable via env):
  AGREE    ~70%
  DISAGREE ~20%
  PARTIAL  ~10%
"""
import asyncio
import random
import logging
import os
from datetime import datetime, timezone
from typing import List

from store import store, HARDCODED_POOLS, CEFS_POOL_ID

logger = logging.getLogger("simulator")

# items annotated per second (per annotator); raise for faster tests
SPEED = float(os.getenv("ANNOTATION_SPEED", "2"))

# Label weights: AGREE, DISAGREE, PARTIAL
_WEIGHTS = [
    float(os.getenv("LABEL_W_AGREE",    "70")),
    float(os.getenv("LABEL_W_DISAGREE", "20")),
    float(os.getenv("LABEL_W_PARTIAL",  "10")),
]
_LABELS = ["AGREE", "DISAGREE", "PARTIAL"]


def _make_label(annotator_seed: int, item_index: int) -> dict:
    """Deterministic-ish label for reproducibility while still looking varied."""
    rng = random.Random(annotator_seed * 10000 + item_index)
    label = rng.choices(_LABELS, weights=_WEIGHTS, k=1)[0]
    confidence = round(rng.uniform(0.55, 0.99), 2)
    note = ""
    if label == "DISAGREE" and rng.random() < 0.4:
        note = rng.choice([
            "Evaluator verdict seems too strict",
            "Edge case not covered by gate",
            "Output is acceptable despite low score",
            "Context was misread by the judge",
        ])
    elif label == "PARTIAL" and rng.random() < 0.6:
        note = rng.choice([
            "Partially agree — gate threshold is borderline",
            "Some criteria met, not all",
            "Quality is acceptable but evaluator was overly generous",
        ])
    return {"label": label, "confidence": confidence, "note": note}


def _collect_items(task: dict) -> List[dict]:
    """Return all annotation items for the given task (from linked dataset)."""
    dataset_id = task.get("dataset_id")
    if not dataset_id:
        return []
    items = []
    for fmeta in store.dataset_files.get(dataset_id, []):
        raw = store.file_items.get(fmeta["id"], [])
        for idx, record in enumerate(raw):
            items.append({
                "id": store.new_id(),
                "file_id": fmeta["id"],
                "file_name": fmeta["name"],
                "index": idx,
                "data": record,
            })
    return items


async def run_simulation(task_id: str) -> None:
    """Main simulation coroutine — spawned when a task is started."""
    task = store.tasks.get(task_id)
    if not task:
        logger.warning("simulate: task %s not found", task_id)
        return

    items = _collect_items(task)
    if not items:
        logger.warning("simulate: task %s has no items, marking DONE immediately", task_id)
        task["state"] = "DONE"
        task["items_total"] = 0
        task["items_done"] = 0
        return

    project_id = task.get("project_id", "")
    project = store.projects.get(project_id, {})
    pool_ids: List[str] = project.get("pool_ids", [CEFS_POOL_ID])
    overlap: int = task.get("overlap", 1)

    # Collect annotators from all attached pools (up to `overlap`)
    annotators = []
    for pid in pool_ids:
        pool = HARDCODED_POOLS.get(pid)
        if pool:
            annotators.extend(pool["members"])
    if not annotators:
        annotators = HARDCODED_POOLS[CEFS_POOL_ID]["members"]
    annotators = annotators[:overlap]  # honour overlap setting

    task["items_total"] = len(items)
    task["items_done"] = 0

    delay_per_item = 1.0 / SPEED if SPEED > 0 else 0.0
    now = datetime.now(timezone.utc)

    for ann_idx, annotator in enumerate(annotators):
        assignment_id = store.new_id()
        ann_items = []

        for item_idx, item in enumerate(items):
            if delay_per_item > 0:
                await asyncio.sleep(delay_per_item / len(annotators))

            # Check if task was stopped
            if store.tasks[task_id]["state"] == "STOPPED":
                logger.info("simulate: task %s stopped during simulation", task_id)
                return

            label_data = _make_label(ann_idx, item_idx)
            ann_items.append({
                "id": item["id"],
                "file_id": item["file_id"],   # needed for dataset export filtering
                "file_name": item["file_name"],
                "data": item["data"],
                "result": label_data,
                "type": "DATA",
                "stat": {"submitted": 1, "accepted": 1, "rejected": 0, "skipped": 0},
            })

            task["items_done"] = min(
                task["items_total"],
                task["items_done"] + (1 / len(annotators)),
            )

        end_time = datetime.now(timezone.utc)
        assignment = {
            "id": assignment_id,
            "task_id": task_id,
            "project_id": project_id,
            "marker_id": annotator["uid"],
            "marker_name": annotator["name"],
            "status": "ACCEPTED",
            "items": ann_items,
            "result": {a["id"]: a["result"] for a in ann_items},
            "start_date": now.isoformat(),
            "end_date": end_time.isoformat(),
            "submitted_at": end_time.isoformat(),
            "accepted_at": end_time.isoformat(),
            "file_url": f"/api/v0/assignments/{assignment_id}/result",
            "is_review_public": False,
        }
        store.assignments[assignment_id] = assignment
        store.task_assignments.setdefault(task_id, []).append(assignment_id)

    task["items_done"] = task["items_total"]
    task["state"] = "DONE"
    task["completed_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("simulate: task %s DONE — %d items × %d annotators",
                task_id, len(items), len(annotators))
