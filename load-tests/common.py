"""Shared helpers for Locust load tests."""

import os
import json
import random
import string

# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8888")

# ---------------------------------------------------------------------------
# Auth helpers (mirrors the artillery before-flow)
# ---------------------------------------------------------------------------
AUTH_REALM = os.getenv("AUTH_REALM", "tagme-public")
AUTH_CLIENT = os.getenv("AUTH_CLIENT_ID", "tagme")
AUTH_USER = os.getenv("AUTH_USERNAME", "")
AUTH_PASS = os.getenv("AUTH_PASSWORD", "")
AUTH_ORG = os.getenv("AUTH_ORG_ID", "")
SKIP_AUTH = os.getenv("SKIP_AUTH", "true").lower() in ("1", "true", "yes")


def obtain_token(client) -> str:
    """Obtain a bearer token via OIDC password grant. Returns empty string if auth is skipped."""
    if SKIP_AUTH:
        return ""
    resp = client.post(
        f"/auth/realms/{AUTH_REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": AUTH_CLIENT,
            "username": AUTH_USER,
            "password": AUTH_PASS,
        },
        headers={
            "Organization-Id": AUTH_ORG,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        name="[auth] obtain_token",
    )
    return resp.json().get("access_token", "")


def auth_headers(token: str) -> dict:
    """Return headers dict, including Authorization if a token exists."""
    h: dict[str, str] = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ---------------------------------------------------------------------------
# Payload generators
# ---------------------------------------------------------------------------
CHAT_PROMPTS = [
    "Say hello in one word.",
    "What is 2+2?",
    "Summarize the theory of relativity in one sentence.",
    "Name three primary colors.",
    "Translate 'good morning' to French.",
    "What is the capital of Japan?",
    "Explain recursion in one sentence.",
    "Write a haiku about load testing.",
]

EMBEDDING_TEXTS = [
    "Load test sample text for embeddings.",
    "The quick brown fox jumps over the lazy dog.",
    "Artificial intelligence is transforming software engineering.",
    "Performance testing is critical for production readiness.",
    "Locust is an open-source load testing framework written in Python.",
]

SAMPLE_VALIDATION_PAYLOAD = {
    "data": [
        {"id": i, "text": f"Sample record {i} for validation gate testing."}
        for i in range(5)
    ]
}


def random_string(length: int = 32) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def chat_payload(model: str = "gpt-4o-mini", max_tokens: int = 50) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": random.choice(CHAT_PROMPTS)}],
        "max_tokens": max_tokens,
    }


def embedding_payload(model: str = "text-embedding-ada-002") -> dict:
    return {
        "model": model,
        "input": random.choice(EMBEDDING_TEXTS),
    }
