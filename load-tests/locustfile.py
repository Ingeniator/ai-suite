"""
AI-Suite — combined Locust load test.

Runs all service user classes together. Usage:

    # Web UI (default)
    locust -f locustfile.py --host http://localhost:8888

    # Headless – 50 users, ramp 5/s, run 2 min
    locust -f locustfile.py --host http://localhost:8888 \
        --headless -u 50 -r 5 -t 2m

Individual services can be targeted by running their files directly:

    locust -f test_yallmp.py --host http://localhost:8888
"""

# Import all user classes so Locust discovers them.
from test_yallmp import YallmpUser          # noqa: F401
from test_checkr import CheckrUser          # noqa: F401
from test_llogr import LlogrUser            # noqa: F401
from test_clickstream import ClickstreamUser  # noqa: F401
from test_gateway import GatewayUser        # noqa: F401
