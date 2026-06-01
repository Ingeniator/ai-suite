"""
In-memory store for CEFS meta-evaluator runs.
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Run:
    id: str
    dataset_id: str          # dataset-mock dataset id
    dataset_name: str
    state: str               # QUEUED|FETCHING|SUBMITTING|ANNOTATING|EVALUATING|DONE|FAILED|CANCELLED
    created_at: str
    updated_at: str

    # annotator-mock resource ids
    am_dataset_id: Optional[str] = None
    am_project_id: Optional[str] = None
    am_task_id: Optional[str] = None

    # progress
    items_total: int = 0
    items_done: int = 0

    # outputs
    error: Optional[str] = None
    metrics: Optional[dict] = None

    # asyncio task handle — excluded from API responses
    _bg_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "dataset_id":    self.dataset_id,
            "dataset_name":  self.dataset_name,
            "state":         self.state,
            "created_at":    self.created_at,
            "updated_at":    self.updated_at,
            "am_dataset_id": self.am_dataset_id,
            "am_project_id": self.am_project_id,
            "am_task_id":    self.am_task_id,
            "items_total":   self.items_total,
            "items_done":    self.items_done,
            "error":         self.error,
            "metrics":       self.metrics,
        }


class RunStore:
    def __init__(self):
        self.runs: Dict[str, Run] = {}
        # dataset IDs already picked up by the auto-poller (reset on restart — in-memory only)
        self.seen_datasets: set = set()

    def new_run(self, dataset_id: str, dataset_name: str) -> Run:
        run_id = str(uuid.uuid4())
        now = _now()
        run = Run(
            id=run_id,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            state="QUEUED",
            created_at=now,
            updated_at=now,
        )
        self.runs[run_id] = run
        return run

    def touch(self, run: Run, state: Optional[str] = None) -> None:
        if state:
            run.state = state
        run.updated_at = _now()


store = RunStore()
