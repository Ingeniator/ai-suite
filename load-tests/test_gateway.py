"""
Load tests for the Nginx gateway itself.

Light-weight probes to measure gateway overhead, routing latency,
and verify X-Request-ID propagation.
"""

from locust import HttpUser, task, between


class GatewayUser(HttpUser):
    wait_time = between(1, 5)
    weight = 1

    @task(3)
    def gateway_health(self):
        with self.client.get("/health", name="/health (gateway)", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status: {resp.status_code}")

    @task(1)
    def root(self):
        self.client.get("/", name="/ (landing)")

    @task(2)
    def request_id_propagation(self):
        """Verify gateway injects X-Request-ID."""
        with self.client.get(
            "/ai/health",
            name="/ai/health (reqid check)",
            catch_response=True,
        ) as resp:
            if "X-Request-ID" in resp.headers or "x-request-id" in resp.headers:
                resp.success()
            else:
                resp.failure("Missing X-Request-ID header")
