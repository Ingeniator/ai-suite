"""Clickstream — Amplitude-compatible event collector backed by ClickHouse."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

# ── Prometheus metrics ──

REQUEST_COUNT = Counter(
    "http_requests_total", "Total number of HTTP requests",
    ["method", "endpoint", "status_code"]
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "Histogram of request processing time",
    ["method", "endpoint"]
)

EVENTS_INGESTED = Counter(
    "clickstream_events_ingested_total", "Total events successfully ingested"
)

EVENTS_DROPPED = Counter(
    "clickstream_events_dropped_total", "Total events dropped (missing user_id/device_id)",
)

CLICKHOUSE_ERRORS = Counter(
    "clickstream_clickhouse_errors_total", "Total ClickHouse write errors",
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method
        endpoint = request.url.path
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=response.status_code).inc()
        REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)
        return response


app = FastAPI(title="clickstream", version="0.1.0")
app.add_middleware(PrometheusMiddleware)

CH_URL = os.environ.get("CLICKSTREAM_CH_URL", "http://clickhouse:8123")
CH_DB = os.environ.get("CLICKSTREAM_CH_DATABASE", "default")
CH_TABLE = os.environ.get("CLICKSTREAM_CH_TABLE", "clickstream_events")
CH_USER = os.environ.get("CLICKSTREAM_CH_USER", "default")
CH_PASSWORD = os.environ.get("CLICKSTREAM_CH_PASSWORD", "")
API_KEY = os.environ.get("CLICKSTREAM_API_KEY", "clickstream-key")

CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {CH_DB}.{CH_TABLE} (
    insert_id    String,
    event_type   String,
    timestamp    DateTime64(3),
    user_id      String DEFAULT '',
    device_id    String DEFAULT '',
    session_id   Int64 DEFAULT 0,
    app_version  String DEFAULT '',
    platform     String DEFAULT '',
    event_properties String DEFAULT '{{}}'  ,
    user_properties  String DEFAULT '{{}}',
    groups       String DEFAULT '{{}}',
    INDEX idx_event_props event_properties TYPE tokenbf_v1(10240, 3, 0) GRANULARITY 4
) ENGINE = MergeTree()
ORDER BY (user_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
"""


def ch_params() -> dict:
    p = {"database": CH_DB, "user": CH_USER}
    if CH_PASSWORD:
        p["password"] = CH_PASSWORD
    return p


@app.on_event("startup")
async def startup():
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CH_URL}/", params={**ch_params(), "query": CREATE_TABLE}
        )
        resp.raise_for_status()
    print(f"Table {CH_DB}.{CH_TABLE} ready")


def ms_to_dt(ms: int | None) -> str:
    """Convert epoch milliseconds to ClickHouse DateTime64 string."""
    if ms is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


# ── Amplitude HTTP V2 API ──

@app.post("/2/httpapi")
async def httpapi(request: Request) -> JSONResponse:
    """Amplitude-compatible event ingestion endpoint."""
    body = await request.json()

    req_key = body.get("api_key") or request.headers.get("Api-Key")
    if req_key != API_KEY:
        return JSONResponse(status_code=401, content={
            "code": 401, "error": "Invalid API key"
        })

    events = body.get("events", [])
    if not events:
        return JSONResponse(content={
            "code": 200, "events_ingested": 0,
            "payload_size_bytes": 0, "server_upload_time": int(time.time() * 1000),
        })

    rows = []
    for ev in events:
        user_id = ev.get("user_id", "") or ""
        device_id = ev.get("device_id", "") or ""
        if not user_id and not device_id:
            EVENTS_DROPPED.inc()
            continue

        rows.append(json.dumps({
            "insert_id": ev.get("insert_id", ""),
            "event_type": ev.get("event_type", ""),
            "timestamp": ms_to_dt(ev.get("time")),
            "user_id": user_id,
            "device_id": device_id,
            "session_id": ev.get("session_id", 0) or 0,
            "app_version": ev.get("app_version", "") or "",
            "platform": ev.get("platform", "") or "",
            "event_properties": json.dumps(ev.get("event_properties", {}), default=str),
            "user_properties": json.dumps(ev.get("user_properties", {}), default=str),
            "groups": json.dumps(ev.get("groups", {}), default=str),
        }))

    if rows:
        data = "\n".join(rows)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{CH_URL}/",
                    params={**ch_params(), "query": f"INSERT INTO {CH_DB}.{CH_TABLE} FORMAT JSONEachRow"},
                    content=data,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
            EVENTS_INGESTED.inc(len(rows))
        except Exception:
            CLICKHOUSE_ERRORS.inc()
            raise

    return JSONResponse(content={
        "code": 200,
        "events_ingested": len(rows),
        "payload_size_bytes": len(json.dumps(body)),
        "server_upload_time": int(time.time() * 1000),
    })


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return StarletteResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
