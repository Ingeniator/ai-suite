"""
annotator-mock — Tagme-compatible annotation service mock.

Implements the minimal API subset needed for the CEFS loop:
  - Dataset management (create, upload files)
  - Project management (create, assign pool)
  - Task lifecycle (create → assign pool → start → poll state → get results)
  - Assignments / results retrieval

All state is in-memory.  The hardcoded annotator pool is always available at
pool-id = a1b2c3d4-e5f6-7890-abcd-ef1234567890.

Port: 8010
"""
import asyncio
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from store import store, HARDCODED_POOLS, CEFS_POOL_ID
from simulator import run_simulation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("annotator-mock")

ROOT_PATH = os.getenv("ROOT_PATH", "")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("annotator-mock started — pool %s always available", CEFS_POOL_ID)
    yield
    # cancel any running simulations on shutdown
    for t in store.sim_tasks.values():
        t.cancel()


app = FastAPI(
    title="Annotator Mock (Tagme-compatible)",
    description=(
        "Mock annotation service for CEFS. Implements the Tagme API subset "
        "needed to submit a dataset for annotation, start annotation, poll status, "
        "and retrieve labelled results (AGREE / DISAGREE / PARTIAL)."
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

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _err(status: int, msg: str):
    raise HTTPException(status_code=status, detail={"error": msg})

def _require(obj: Optional[Any], name: str):
    if obj is None:
        _err(404, f"{name} not found")
    return obj


# ── Pydantic request bodies ───────────────────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    organization_id: Optional[str] = None


class TaskCreateRequest(BaseModel):
    project_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    overlap: int = 1
    priority: Optional[int] = None
    # extension: link dataset at creation time
    dataset_id: Optional[str] = None


class DatasetCreateRequest(BaseModel):
    name: str
    access: str = "organization"
    data_source: Optional[str] = None
    data_classification_level: Optional[str] = None


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "service": "annotator-mock"}


@app.get("/api/v0/pools/hardcoded", tags=["meta"])
def get_hardcoded_pool_id():
    """Convenience endpoint — returns the always-available pool ID."""
    return {"pool_id": CEFS_POOL_ID}


# ── Pools ─────────────────────────────────────────────────────────────────────

@app.get("/api/v0/pools", tags=["PoolsApi"])
def list_pools():
    items = list(HARDCODED_POOLS.values())
    return {"items": items, "has_next": False}


@app.get("/api/v0/pool/{pool_id}", tags=["PoolsApi"])
def get_pool(pool_id: str):
    pool = HARDCODED_POOLS.get(pool_id)
    _require(pool, "Pool")
    return pool


@app.get("/api/v0/pool/{pool_id}/members", tags=["PoolsApi"])
def list_pool_members(pool_id: str):
    pool = _require(HARDCODED_POOLS.get(pool_id), "Pool")
    return {"items": pool["members"], "has_next": False}


# ── Datasets ──────────────────────────────────────────────────────────────────

@app.post("/api/v0/datasets", status_code=201, tags=["DatasetsApi"])
def create_dataset(body: DatasetCreateRequest):
    ds_id = store.new_id()
    ds = {
        "id": ds_id,
        "name": body.name,
        "access": body.access,
        "data_source": body.data_source,
        "data_classification_level": body.data_classification_level,
        "owner_id": "00000000-0000-0000-0000-000000000000",
        "created_date": _now(),
        "files_count": 0,
    }
    store.datasets[ds_id] = ds
    store.dataset_files[ds_id] = []
    logger.info("dataset created: %s (%s)", ds_id, body.name)
    return ds


@app.get("/api/v0/datasets", tags=["DatasetsApi"])
def list_datasets(page: int = 1, size: int = 50):
    items = list(store.datasets.values())
    start = (page - 1) * size
    page_items = items[start : start + size]
    return {"items": page_items, "has_next": start + size < len(items)}


@app.get("/api/v0/datasets/{dataset_id}", tags=["DatasetsApi"])
def get_dataset(dataset_id: str):
    ds = _require(store.datasets.get(dataset_id), "Dataset")
    ds["files_count"] = len(store.dataset_files.get(dataset_id, []))
    return ds


@app.post("/api/v0/datasets/{dataset_id}/files", status_code=201, tags=["DatasetsApi"])
async def upload_dataset_file(
    dataset_id: str,
    file: UploadFile = File(...),
):
    """
    Upload a JSON or JSONL file into a dataset.
    Each JSON-array element or JSONL line becomes one annotation item.

    Accepted formats
    ----------------
    • JSON array:  [{"trace_id": "...", ...}, ...]
    • JSONL:       one JSON object per line
    """
    _require(store.datasets.get(dataset_id), "Dataset")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        _err(400, "File must be UTF-8 encoded")

    items: List[dict] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = [parsed]
        else:
            _err(400, "JSON file must be an object or array")
    except json.JSONDecodeError:
        # try JSONL
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                _err(400, f"Invalid JSON on line {lineno}")

    if not items:
        _err(400, "File contains no items")

    file_id = store.new_id()
    file_meta = {
        "id": file_id,
        "dataset_id": dataset_id,
        "name": file.filename or f"upload-{file_id}.json",
        "created_date": _now(),
        "owner_id": "00000000-0000-0000-0000-000000000000",
        "url": f"/api/v0/files/{file_id}",
        "size": len(content),
    }
    store.dataset_files[dataset_id].append(file_meta)
    store.file_items[file_id] = items
    store.datasets[dataset_id]["files_count"] = len(store.dataset_files[dataset_id])
    logger.info("file %s uploaded to dataset %s — %d items", file_id, dataset_id, len(items))
    return file_meta


@app.get("/api/v0/datasets/{dataset_id}/files/info", tags=["DatasetsApi"])
def list_dataset_files(dataset_id: str, page: int = 1, size: int = 50):
    _require(store.datasets.get(dataset_id), "Dataset")
    files = store.dataset_files.get(dataset_id, [])
    start = (page - 1) * size
    page_files = files[start : start + size]
    return {"items": page_files, "has_next": start + size < len(files)}


@app.get("/api/v0/files/{file_id}/info", tags=["DatasetsApi"])
def get_file_info(file_id: str):
    for ds_files in store.dataset_files.values():
        for f in ds_files:
            if f["id"] == file_id:
                return f
    _err(404, "File not found")


@app.get("/api/v0/datasets/{dataset_id}/export", tags=["DatasetsApi"])
def export_dataset(
    dataset_id: str,
    format: str = Query("json", enum=["json", "csv", "tsv", "xls"]),
):
    """
    Export annotation results for all completed assignments whose items
    belong to this dataset.  Returns JSONL by default; CSV/TSV are also
    supported.  'xls' falls back to CSV for simplicity.
    """
    _require(store.datasets.get(dataset_id), "Dataset")

    # Collect all assignment results touching this dataset
    file_ids = {f["id"] for f in store.dataset_files.get(dataset_id, [])}
    rows: List[dict] = []

    for asgn in store.assignments.values():
        for item in asgn.get("items", []):
            if item.get("file_id") not in file_ids:
                continue
            rows.append({
                "item_id": item["id"],
                "file_id": item.get("file_id"),
                "file_name": item["file_name"],
                "annotator_id": asgn["marker_id"],
                "annotator_name": asgn.get("marker_name", ""),
                "assignment_id": asgn["id"],
                "task_id": asgn["task_id"],
                "label": item["result"]["label"],
                "confidence": item["result"]["confidence"],
                "note": item["result"]["note"],
                "submitted_at": asgn.get("submitted_at", ""),
                # original trace data passthrough
                **{f"data_{k}": v for k, v in (item.get("data") or {}).items()
                   if isinstance(v, (str, int, float, bool)) or v is None},
            })

    if format == "json":
        return JSONResponse(content=rows)

    if format in ("csv", "tsv", "xls"):
        sep = "\t" if format == "tsv" else ","
        if not rows:
            return StreamingResponse(
                io.BytesIO(b""),
                media_type="text/plain",
                headers={"Content-Disposition": f'attachment; filename="export.{format}"'},
            )
        headers = list(rows[0].keys())
        lines = [sep.join(headers)]
        for row in rows:
            lines.append(sep.join(str(row.get(h, "")) for h in headers))
        content = "\n".join(lines).encode("utf-8")
        media = "text/tab-separated-values" if format == "tsv" else "text/csv"
        return StreamingResponse(
            io.BytesIO(content),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="export.{format}"'},
        )

    _err(400, f"Unknown format: {format}")


# ── Projects ──────────────────────────────────────────────────────────────────

@app.post("/api/v0/markup_project", status_code=201, tags=["ProjectApi"])
def create_project(body: ProjectCreateRequest):
    uid = store.new_id()
    project = {
        "uid": uid,
        "name": body.name,
        "description": body.description,
        "organization_id": body.organization_id,
        "created_at": _now(),
        "pool_ids": [],
        "tasks_count": 0,
        "status": "ACTIVE",
    }
    store.projects[uid] = project
    logger.info("project created: %s (%s)", uid, body.name)
    return project


@app.get("/api/v0/markup_project", tags=["ProjectApi"])
def list_projects(page: int = 1, size: int = 50, archived: Optional[bool] = None):
    items = list(store.projects.values())
    if archived is not None:
        target = "ARCHIVED" if archived else "ACTIVE"
        items = [p for p in items if p.get("status") == target]
    start = (page - 1) * size
    return {"items": items[start : start + size], "has_next": start + size < len(items)}


@app.get("/api/v0/markup_project/{uid}", tags=["ProjectApi"])
def get_project(uid: str):
    return _require(store.projects.get(uid), "Project")


@app.post("/api/v0/markup_project/{uid}/pools/{pool_id}", status_code=204, tags=["ProjectApi"])
def assign_pool_to_project(uid: str, pool_id: str):
    project = _require(store.projects.get(uid), "Project")
    if pool_id not in HARDCODED_POOLS:
        _err(404, f"Pool {pool_id} not found")
    if pool_id not in project["pool_ids"]:
        project["pool_ids"].append(pool_id)
    logger.info("pool %s assigned to project %s", pool_id, uid)
    # 204 No Content — return nothing
    from fastapi.responses import Response
    return Response(status_code=204)


@app.delete("/api/v0/markup_project/{uid}/pools/{pool_id}", status_code=204, tags=["ProjectApi"])
def remove_pool_from_project(uid: str, pool_id: str):
    project = _require(store.projects.get(uid), "Project")
    project["pool_ids"] = [p for p in project["pool_ids"] if p != pool_id]
    from fastapi.responses import Response
    return Response(status_code=204)


@app.put("/api/v0/markup_project/{uid}", tags=["ProjectApi"])
def update_project(uid: str, body: Dict[str, Any]):
    project = _require(store.projects.get(uid), "Project")
    for k, v in body.items():
        if k not in ("uid", "created_at"):
            project[k] = v
    return project


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.post("/api/v0/tasks", status_code=201, tags=["TasksApi"])
def create_task(body: TaskCreateRequest):
    _require(store.projects.get(body.project_id), "Project")
    if body.dataset_id:
        _require(store.datasets.get(body.dataset_id), "Dataset")

    task_id = store.new_id()
    task = {
        "uid": task_id,
        "name": body.name or f"task-{task_id[:8]}",
        "description": body.description,
        "project_id": body.project_id,
        "dataset_id": body.dataset_id,
        "overlap": body.overlap,
        "priority": body.priority,
        "state": "INITIAL",
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "items_total": 0,
        "items_done": 0,
    }
    store.tasks[task_id] = task
    store.task_assignments[task_id] = []

    # update project tasks count
    store.projects[body.project_id]["tasks_count"] = (
        store.projects[body.project_id].get("tasks_count", 0) + 1
    )
    logger.info("task created: %s in project %s", task_id, body.project_id)
    return task


@app.get("/api/v0/tasks", tags=["TasksApi"])
def list_tasks(
    project_id: Optional[str] = None,
    page: int = 1,
    size: int = 50,
    state: Optional[List[str]] = Query(default=None, alias="state[]"),
):
    items = list(store.tasks.values())
    if project_id:
        items = [t for t in items if t["project_id"] == project_id]
    if state:
        items = [t for t in items if t["state"] in state]
    start = (page - 1) * size
    return {"items": items[start : start + size], "has_next": start + size < len(items)}


@app.get("/api/v0/tasks/{uid}", tags=["TasksApi"])
def get_task(uid: str):
    return _require(store.tasks.get(uid), "Task")


@app.get("/api/v0/tasks/{uid}/state", tags=["TasksApi"])
def get_task_state(uid: str):
    task = _require(store.tasks.get(uid), "Task")
    return {"state": task["state"]}


@app.post("/api/v0/tasks/{uid}/start", tags=["TasksApi"])
async def start_task(uid: str):
    task = _require(store.tasks.get(uid), "Task")
    if task["state"] == "RUNNING":
        return task  # idempotent
    if task["state"] == "DONE":
        _err(422, "Task is already DONE")
    if task["state"] == "ARCHIVE":
        _err(422, "Task is archived")

    task["state"] = "RUNNING"
    task["started_at"] = _now()

    # spawn background simulation (requires async context)
    sim = asyncio.create_task(run_simulation(uid))
    store.sim_tasks[uid] = sim
    logger.info("task %s started — simulation spawned", uid)
    return task


@app.post("/api/v0/tasks/{uid}/stop", tags=["TasksApi"])
def stop_task(uid: str):
    task = _require(store.tasks.get(uid), "Task")
    if task["state"] != "RUNNING":
        _err(422, f"Cannot stop task in state {task['state']}")
    task["state"] = "STOPPED"
    sim = store.sim_tasks.pop(uid, None)
    if sim:
        sim.cancel()
    return task


@app.post("/api/v0/tasks/{uid}/complete", tags=["TasksApi"])
def complete_task(uid: str):
    task = _require(store.tasks.get(uid), "Task")
    task["state"] = "DONE"
    task["completed_at"] = _now()
    return task


@app.post("/api/v0/tasks/{uid}/remove", status_code=204, tags=["TasksApi"])
def remove_task(uid: str):
    _require(store.tasks.get(uid), "Task")
    task = store.tasks.pop(uid)
    task["state"] = "ARCHIVE"
    from fastapi.responses import Response
    return Response(status_code=204)


# ── Assignments / results ─────────────────────────────────────────────────────

@app.get("/api/v0/assignments", tags=["AssignmentsApi"])
def list_assignments(
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    size: int = 100,
):
    """
    Return assignments, optionally filtered by task or project.
    This is the primary endpoint for retrieving annotation results.
    """
    if task_id:
        ids = store.task_assignments.get(task_id, [])
        items = [store.assignments[i] for i in ids if i in store.assignments]
    else:
        items = list(store.assignments.values())

    if project_id:
        items = [a for a in items if a.get("project_id") == project_id]
    if status:
        items = [a for a in items if a.get("status") == status]

    start = (page - 1) * size
    page_items = items[start : start + size]
    return {"items": page_items, "has_next": start + size < len(items)}


@app.get("/api/v0/assignments/{assignment_id}", tags=["AssignmentsApi"])
def get_assignment(assignment_id: str):
    return _require(store.assignments.get(assignment_id), "Assignment")


# ── Statistics ────────────────────────────────────────────────────────────────

@app.get("/api/v0/statistics/task/{task_id}", tags=["StatisticsApi"])
def task_statistics(task_id: str):
    task = _require(store.tasks.get(task_id), "Task")
    asgn_ids = store.task_assignments.get(task_id, [])
    assignments = [store.assignments[i] for i in asgn_ids if i in store.assignments]

    accepted = sum(1 for a in assignments if a["status"] == "ACCEPTED")
    submitted = sum(1 for a in assignments if a["status"] in ("SUBMITTED", "ACCEPTED"))

    return {
        "task_id": task_id,
        "state": task["state"],
        "items_total": task.get("items_total", 0),
        "items_done": int(task.get("items_done", 0)),
        "items_pending": max(0, task.get("items_total", 0) - int(task.get("items_done", 0))),
        "assignments_total": len(assignments),
        "accepted": accepted,
        "submitted": submitted,
        "rejected": 0,
        "assigned": len(assignments) - submitted,
        "skipped": 0,
    }


@app.get("/api/v0/statistics/project/{project_id}", tags=["StatisticsApi"])
def project_statistics(project_id: str):
    project = _require(store.projects.get(project_id), "Project")
    tasks = [t for t in store.tasks.values() if t["project_id"] == project_id]
    pool_ids = project.get("pool_ids", [])
    return {
        "project_id": project_id,
        "tasks_count": len(tasks),
        "objects_count": sum(t.get("items_total", 0) for t in tasks),
        "marked_count": sum(int(t.get("items_done", 0)) for t in tasks),
        "pools_count": len(pool_ids),
        "markers_pools_count": len(pool_ids),
        "validators_pools_count": 0,
        "markers_count": sum(
            HARDCODED_POOLS[p]["members_count"]
            for p in pool_ids
            if p in HARDCODED_POOLS
        ),
        "validators_count": 0,
        "active_marker_count": 0,
        "status": project.get("status", "ACTIVE"),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8010"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
