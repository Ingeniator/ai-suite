"""
Load tests for CHECKR (Validation Engine) — routed via /validators/*.

Covers: validator listing, info, single-gate validation, multi-gate batch,
and G-Eval.
"""

import random
from locust import HttpUser, task, between

from common import auth_headers, obtain_token, SAMPLE_VALIDATION_PAYLOAD


GATES = ["gate1", "gate2", "gate3", "gate4", "gate5", "gate6", "gate7", "gate8"]


class CheckrUser(HttpUser):
    wait_time = between(1, 3)
    weight = 3

    def on_start(self):
        self.token = obtain_token(self.client)
        self.headers = auth_headers(self.token)

    @task(1)
    def list_validators(self):
        self.client.get(
            "/validators/api/v0/list",
            headers=self.headers,
            name="/validators/api/v0/list",
        )

    @task(1)
    def validator_info(self):
        gate = random.choice(GATES)
        self.client.get(
            f"/validators/api/v0/info/{gate}",
            headers=self.headers,
            name="/validators/api/v0/info/[gate]",
        )

    @task(3)
    def validate_single_gate(self):
        gate = random.choice(GATES)
        self.client.post(
            f"/validators/api/v0/validate/{gate}",
            json=SAMPLE_VALIDATION_PAYLOAD,
            headers=self.headers,
            name="/validators/api/v0/validate/[gate]",
        )

    @task(2)
    def validate_multi_gate(self):
        gates = random.sample(GATES, k=random.randint(2, 4))
        self.client.post(
            "/validators/api/v0/validate",
            json={
                "validators": gates,
                **SAMPLE_VALIDATION_PAYLOAD,
            },
            headers=self.headers,
            name="/validators/api/v0/validate (batch)",
        )

    @task(2)
    def g_eval(self):
        self.client.post(
            "/validators/api/v0/g-eval",
            json={
                "prompt": "Rate the quality of this text on a scale of 1-5.",
                "response": "This is a well-structured paragraph with clear reasoning.",
            },
            headers=self.headers,
            name="/validators/api/v0/g-eval",
        )

    @task(1)
    def health(self):
        self.client.get("/validators/livez", name="/validators/livez")
