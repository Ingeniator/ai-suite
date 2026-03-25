"""
Predefined load profiles that can be used with Locust's LoadTestShape.

Usage:
    locust -f locustfile.py,profiles.py --host http://localhost:8888

Profiles:
  - SmokeShape:    1 user, 30 s  — sanity check
  - LoadShape:     50 users, ramp 5/s, 2 min sustained
  - StressShape:   200 users, ramp 10/s, 3 min sustained
  - SpikeShape:    5 → 100 → 5 users (sudden spike)
  - SoakShape:     30 users, 10 min sustained — find memory leaks
"""

import os
import math
from locust import LoadTestShape

PROFILE = os.getenv("LOAD_PROFILE", "load").lower()


class _BaseShape(LoadTestShape):
    """Override tick() to return (user_count, spawn_rate) or None to stop."""
    stages: list[dict] = []

    def tick(self):
        run_time = self.get_run_time()
        elapsed = 0
        for stage in self.stages:
            elapsed += stage["duration"]
            if run_time < elapsed:
                return (stage["users"], stage["spawn_rate"])
        return None


class SmokeShape(_BaseShape):
    stages = [
        {"duration": 30, "users": 1, "spawn_rate": 1},
    ]


class LoadShape(_BaseShape):
    stages = [
        {"duration": 30, "users": 10, "spawn_rate": 2},   # warm up
        {"duration": 30, "users": 50, "spawn_rate": 5},   # ramp
        {"duration": 120, "users": 50, "spawn_rate": 5},   # sustained
    ]


class StressShape(_BaseShape):
    stages = [
        {"duration": 30, "users": 20, "spawn_rate": 5},
        {"duration": 60, "users": 100, "spawn_rate": 10},
        {"duration": 60, "users": 200, "spawn_rate": 10},
        {"duration": 180, "users": 200, "spawn_rate": 10},
    ]


class SpikeShape(_BaseShape):
    stages = [
        {"duration": 30, "users": 5, "spawn_rate": 5},
        {"duration": 10, "users": 100, "spawn_rate": 100},  # spike!
        {"duration": 60, "users": 100, "spawn_rate": 100},
        {"duration": 10, "users": 5, "spawn_rate": 100},    # drop
        {"duration": 60, "users": 5, "spawn_rate": 5},
    ]


class SoakShape(_BaseShape):
    stages = [
        {"duration": 60, "users": 30, "spawn_rate": 5},    # ramp
        {"duration": 600, "users": 30, "spawn_rate": 5},   # soak 10 min
    ]


# Map names to classes for env-var selection
_PROFILES = {
    "smoke": SmokeShape,
    "load": LoadShape,
    "stress": StressShape,
    "spike": SpikeShape,
    "soak": SoakShape,
}


# When this module is loaded alongside locustfile.py, Locust picks up
# the *last* LoadTestShape subclass. We dynamically create the chosen
# profile class so it's the one Locust finds.
_chosen = _PROFILES.get(PROFILE, LoadShape)
if _chosen is not LoadShape:
    # Create an alias so Locust discovers it
    class ActiveShape(_chosen):  # type: ignore[misc]
        pass
