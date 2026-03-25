"""
Load tests for CLICKSTREAM (Event Collector).

Sends Amplitude-compatible events at high throughput.
"""

import time
import random
import uuid
from locust import HttpUser, task, between

from common import random_string


EVENT_TYPES = [
    "page_view",
    "button_click",
    "form_submit",
    "search",
    "model_run",
    "validation_request",
    "export",
    "login",
]


def _amplitude_payload(n_events: int = 1) -> dict:
    """Build an Amplitude-compatible event batch."""
    return {
        "api_key": "locust-load-test",
        "events": [
            {
                "event_type": random.choice(EVENT_TYPES),
                "user_id": f"locust-user-{random.randint(1, 100)}",
                "device_id": str(uuid.uuid4()),
                "time": int(time.time() * 1000),
                "event_properties": {
                    "source": "locust",
                    "session": random_string(16),
                    "value": random.random(),
                },
            }
            for _ in range(n_events)
        ],
    }


class ClickstreamUser(HttpUser):
    wait_time = between(0.5, 2)
    weight = 2

    @task(5)
    def send_single_event(self):
        self.client.post(
            "/2/httpapi",
            json=_amplitude_payload(1),
            name="/2/httpapi (single)",
        )

    @task(3)
    def send_batch_events(self):
        self.client.post(
            "/2/httpapi",
            json=_amplitude_payload(random.randint(5, 20)),
            name="/2/httpapi (batch)",
        )

    @task(1)
    def health(self):
        self.client.get("/health", name="/clickstream/health")
