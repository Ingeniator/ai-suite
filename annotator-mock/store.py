"""
In-memory state store for annotator-mock.
"""
from typing import Dict, List, Any, Optional
import uuid

# ── Hardcoded annotator pool ──────────────────────────────────────────────────

CEFS_POOL_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

_ANNOTATORS = [
    {
        "uid": "b1c2d3e4-f5a6-7890-bcde-f12345678901",
        "name": "Anna Reviewer",
        "email": "anna@cefs.internal",
        "person_id": "b1c2d3e4-f5a6-7890-bcde-f12345678901",
    },
    {
        "uid": "c2d3e4f5-a6b7-8901-cdef-123456789012",
        "name": "Boris Analyst",
        "email": "boris@cefs.internal",
        "person_id": "c2d3e4f5-a6b7-8901-cdef-123456789012",
    },
    {
        "uid": "d3e4f5a6-b7c8-9012-def0-234567890123",
        "name": "Carla Expert",
        "email": "carla@cefs.internal",
        "person_id": "d3e4f5a6-b7c8-9012-def0-234567890123",
    },
]

HARDCODED_POOLS: Dict[str, dict] = {
    CEFS_POOL_ID: {
        "uid": CEFS_POOL_ID,
        "name": "CEFS Annotation Pool",
        "group_type": "MARKERS",
        "access": "organization",
        "acl": None,
        "person_id": _ANNOTATORS[0]["uid"],
        "members": _ANNOTATORS,
        "members_count": len(_ANNOTATORS),
    }
}


# ── In-memory store ───────────────────────────────────────────────────────────

class Store:
    """All mutable state lives here. Not thread-safe; fine for a single-process mock."""

    def __init__(self) -> None:
        # Projects: project_id -> project dict
        self.projects: Dict[str, dict] = {}

        # Tasks: task_id -> task dict
        self.tasks: Dict[str, dict] = {}

        # Datasets: dataset_id -> dataset dict
        self.datasets: Dict[str, dict] = {}

        # Dataset files: dataset_id -> [file dict, ...]
        self.dataset_files: Dict[str, List[dict]] = {}

        # Raw content of each uploaded file: file_id -> list[item dict]
        self.file_items: Dict[str, List[dict]] = {}

        # Assignments: assignment_id -> assignment dict
        self.assignments: Dict[str, dict] = {}

        # Index: task_id -> [assignment_id, ...]
        self.task_assignments: Dict[str, List[str]] = {}

        # Background simulation handles: task_id -> asyncio.Task
        self.sim_tasks: Dict[str, Any] = {}

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())


store = Store()
