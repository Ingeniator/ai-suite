# Load Tests

Intensive load testing for AI-Suite services using [Locust](https://locust.io/).

## Services covered

| File | Service | Key scenarios |
|------|---------|---------------|
| `test_yallmp.py` | YALLMP (LLM Proxy) | Chat completions, models listing, embeddings, health, dashboard |
| `test_checkr.py` | CHECKR (Validators) | Single/multi-gate validation, G-Eval, validator info |
| `test_llogr.py` | LLOGR (Traces) | Langfuse ingestion, log listing, search, presigned URLs |
| `test_clickstream.py` | Clickstream | Single & batch Amplitude event ingestion |
| `test_gateway.py` | Nginx Gateway | Health, routing latency, X-Request-ID propagation |

`locustfile.py` combines all services into a single run with weighted distribution.

## Quick start

```bash
cd load-tests
pip install -r requirements.txt

# Open Web UI at http://localhost:8089
make run

# Or run headless
make load
```

## Load profiles

| Profile | Users | Duration | Use case |
|---------|-------|----------|----------|
| `make smoke` | 1 | 30s | Sanity check — verify endpoints respond |
| `make load` | 50 | 2 min | Standard load — baseline performance |
| `make stress` | 200 | 5 min | Stress — find breaking points |
| `make spike` | 5→100→5 | ~3 min | Spike — sudden traffic burst |
| `make soak` | 30 | 10 min | Soak — find memory leaks, connection exhaustion |

Custom runs:

```bash
make headless USERS=100 RATE=10 DURATION=5m
```

Shaped profiles (via `profiles.py`):

```bash
LOAD_PROFILE=spike locust -f locustfile.py,profiles.py --host http://localhost:8888 --headless
```

## Test a single service

```bash
make single-service SVC=test_yallmp.py USERS=20 DURATION=1m
```

Or directly:

```bash
locust -f test_checkr.py --host http://localhost:8888 --headless -u 30 -r 5 -t 2m
```

## Distributed mode (Docker)

Run a Locust cluster alongside the AI-Suite stack:

```bash
# Start 4 workers
make docker WORKERS=4

# Web UI at http://localhost:8089

# Stop
make docker-down
```

This uses `docker-compose.locust.yaml` which joins the `ai-suite_default` network, so Locust workers hit Nginx directly (no port mapping needed).

## Authentication

Auth is **skipped by default** (`SKIP_AUTH=true`). To enable OIDC token flow:

```bash
export SKIP_AUTH=false
export AUTH_USERNAME=your-user
export AUTH_PASSWORD=your-pass
export AUTH_ORG_ID=your-org-id
# optional overrides:
export AUTH_REALM=public
export AUTH_CLIENT_ID=service
```

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `GATEWAY_URL` | `http://localhost:8888` | Target host (also settable via `--host`) |
| `SKIP_AUTH` | `true` | Skip OIDC token acquisition |
| `AUTH_USERNAME` | — | OIDC username |
| `AUTH_PASSWORD` | — | OIDC password |
| `AUTH_ORG_ID` | — | Organization ID header |
| `AUTH_REALM` | `public` | Keycloak realm |
| `AUTH_CLIENT_ID` | `service` | OIDC client ID |
| `LOAD_PROFILE` | `load` | Profile for `profiles.py` (`smoke`/`load`/`stress`/`spike`/`soak`) |

## Project structure

```
load-tests/
├── Makefile                      # Quick-run targets
├── requirements.txt              # Python dependencies
├── common.py                     # Shared auth, payload generators, constants
├── locustfile.py                 # Combined entry point (all services)
├── profiles.py                   # LoadTestShape classes for shaped runs
├── docker-compose.locust.yaml    # Distributed Locust cluster
├── test_yallmp.py                # YALLMP load tests
├── test_checkr.py                # CHECKR load tests
├── test_llogr.py                 # LLOGR load tests
├── test_clickstream.py           # Clickstream load tests
└── test_gateway.py               # Gateway load tests
```
