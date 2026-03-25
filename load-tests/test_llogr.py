"""
Load tests for LLOGR (Trace Collection / Log Browser) — routed via /llogr/*.

Covers: trace ingestion (Langfuse JSON + OTLP), log listing, search, and
presigned URL generation.
"""

import time
import uuid
import random
from locust import HttpUser, task, between

from common import auth_headers, obtain_token, random_string


def _trace_event(trace_id: str) -> dict:
    """Build a minimal Langfuse-style trace/span event."""
    return {
        "batch": [
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "body": {
                    "id": trace_id,
                    "name": f"locust-load-test-{random_string(8)}",
                    "input": {"text": "Load test input"},
                    "output": {"text": "Load test output"},
                    "metadata": {"source": "locust"},
                },
            }
        ]
    }


class LlogrUser(HttpUser):
    wait_time = between(1, 3)
    weight = 2

    def on_start(self):
        self.token = obtain_token(self.client)
        self.headers = auth_headers(self.token)

    # ---- Ingestion ----

    @task(4)
    def ingest_langfuse(self):
        trace_id = str(uuid.uuid4())
        self.client.post(
            "/llogr/api/public/ingestion",
            json=_trace_event(trace_id),
            headers=self.headers,
            name="/llogr/api/public/ingestion",
        )

    # ---- Read paths ----

    @task(2)
    def list_logs(self):
        self.client.get(
            "/llogr/api/public/logs",
            headers=self.headers,
            name="/llogr/api/public/logs",
        )

    @task(2)
    def list_log_keys(self):
        self.client.get(
            "/llogr/api/public/logs/list",
            headers=self.headers,
            name="/llogr/api/public/logs/list",
        )

    @task(2)
    def search_logs(self):
        q = random.choice(["error", "load", "test", "locust", "trace"])
        self.client.get(
            f"/llogr/api/public/logs/search?q={q}",
            headers=self.headers,
            name="/llogr/api/public/logs/search",
        )

    @task(1)
    def health(self):
        self.client.get("/llogr/livez", name="/llogr/livez")
