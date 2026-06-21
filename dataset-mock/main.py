"""
dataset-mock — standalone mock of the dataset service + Keycloak token endpoint.

All state is in-memory. Uploaded files are stored on disk under MOCK_UPLOAD_DIR.

Port: 9100

Add to databridge config.yaml:
    datasinks:
      - name: "dataset-mock"
        type: dataset-mock
        url: "http://dataset-mock:9100"

Inspect uploaded files:
    GET http://localhost:9100/_mock/datasets          — list datasets
    GET http://localhost:9100/_mock/datasets/{id}     — dataset detail + file list
    GET http://localhost:9100/_mock/files/{id}        — download uploaded file
    DELETE http://localhost:9100/_mock/datasets       — wipe all state
"""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="mock-dataset-service")

# ── In-memory store ──────────────────────────────────────────────────────────

_datasets: dict[str, dict] = {}  # id → {name, access, dataset_type, files: [...]}
_files: dict[str, dict] = {}     # file_id → {name, dataset_id, path, size}

_UPLOAD_DIR = Path(os.environ.get("MOCK_UPLOAD_DIR", "/tmp/mock-dataset-service"))


# ── Token endpoint (Keycloak-compatible) ─────────────────────────────────────

@app.post("/realms/{realm}/protocol/openid-connect/token")
async def token(realm: str):
    return {
        "access_token": f"mock-token-{uuid.uuid4().hex[:8]}",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "profile email",
    }


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-dataset-service"}


# ── Dataset endpoints ─────────────────────────────────────────────────────────

class CreateDatasetBody(BaseModel):
    name: str
    access: str = "organization"
    dataset_type: str = "DATASET"
    data_source: str | None = None
    data_classification_level: str | None = None


def _require_bearer(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")


@app.post("/api/v0/datasets", status_code=201)
async def create_dataset(
    body: CreateDatasetBody,
    authorization: str | None = Header(default=None),
):
    _require_bearer(authorization)
    dataset_id = str(uuid.uuid4())
    _datasets[dataset_id] = {
        "id": dataset_id,
        "name": body.name,
        "access": body.access,
        "dataset_type": body.dataset_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
    }
    return _datasets[dataset_id]


@app.get("/api/v0/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    authorization: str | None = Header(default=None),
):
    _require_bearer(authorization)
    if dataset_id not in _datasets:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _datasets[dataset_id]


@app.post("/api/v0/datasets/{dataset_id}/files", status_code=201)
async def upload_file(
    dataset_id: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    _require_bearer(authorization)
    if dataset_id not in _datasets:
        raise HTTPException(status_code=404, detail="Dataset not found")

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    dest = _UPLOAD_DIR / file_id
    content = await file.read()
    dest.write_bytes(content)

    file_meta = {
        "id": file_id,
        "dataset_id": dataset_id,
        "name": file.filename,
        "size": len(content),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _files[file_id] = {**file_meta, "path": str(dest)}
    _datasets[dataset_id]["files"].append(file_meta)

    print(f"  [upload] dataset={dataset_id} file={file.filename} size={len(content):,}B id={file_id}")
    return file_meta


# ── Debug / inspection endpoints ──────────────────────────────────────────────

@app.get("/_mock/datasets")
async def list_datasets():
    return {"datasets": list(_datasets.values()), "count": len(_datasets)}


@app.delete("/_mock/datasets", status_code=200)
async def clean_datasets():
    count = len(_datasets)
    _datasets.clear()
    _files.clear()
    for f in _UPLOAD_DIR.glob("*"):
        f.unlink(missing_ok=True)
    return {"deleted": count}


@app.get("/_mock/datasets/{dataset_id}")
async def inspect_dataset(dataset_id: str):
    if dataset_id not in _datasets:
        raise HTTPException(status_code=404)
    return _datasets[dataset_id]


@app.get("/_mock/files/{file_id}")
async def download_file(file_id: str):
    if file_id not in _files:
        raise HTTPException(status_code=404)
    meta = _files[file_id]
    return FileResponse(meta["path"], filename=meta["name"])


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 9100)))
    parser.add_argument("--upload-dir", default=str(_UPLOAD_DIR))
    args = parser.parse_args()

    _UPLOAD_DIR = Path(args.upload_dir)

    print(f"\nMock dataset service running on http://localhost:{args.port}")
    print(f"Uploads stored in: {_UPLOAD_DIR}")
    print()

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
