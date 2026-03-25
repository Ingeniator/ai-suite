"""
Load tests for YALLMP (LLM Proxy) — routed via /ai/*.

Scenarios mirror the existing artillery tests with weighted distribution:
  - chat completions  (weight 5)
  - list models        (weight 2)
  - embeddings         (weight 2)
  - health check       (weight 1)
"""

import time
from locust import HttpUser, task, between, events

from common import auth_headers, obtain_token, chat_payload, embedding_payload


class YallmpUser(HttpUser):
    wait_time = between(1, 3)
    weight = 5  # most traffic goes here

    def on_start(self):
        self.token = obtain_token(self.client)
        self.headers = auth_headers(self.token)

    # ---- tasks (weights match artillery scenario weights) ----

    @task(5)
    def chat_completions(self):
        self.client.post(
            "/ai/llm/v1/chat/completions",
            json=chat_payload(),
            headers=self.headers,
            name="/ai/llm/v1/chat/completions",
        )

    @task(2)
    def list_models(self):
        self.client.get(
            "/ai/llm/v1/models",
            headers=self.headers,
            name="/ai/llm/v1/models",
        )

    @task(2)
    def embeddings(self):
        self.client.post(
            "/ai/llm/v1/embeddings",
            json=embedding_payload(),
            headers=self.headers,
            name="/ai/llm/v1/embeddings",
        )

    @task(1)
    def health(self):
        self.client.get("/ai/health", name="/ai/health")

    @task(1)
    def dashboard(self):
        self.client.get(
            "/ai/dashboard",
            headers=self.headers,
            name="/ai/dashboard",
        )
