# Integration Tests Plan

Tests live in `ai-suite/tests/`, run as a docker-compose service under the `test` profile.

## Open questions

- **Typical-agent tests** require a live LLM (LM Studio). Should those use a separate `pytest.mark` so they can be skipped when no LLM is available, or is LM Studio always up in the test env?
- **Checkr** — g-eval/rubric-eval tests call an LLM. Do we want those, or only structural/job-lifecycle tests (LLM-free)?
- **Scope**: start with scenarios 1–3 (health + tracing pipeline + typical-agent), or all 5 at once?

---

## Scenario 1 · Health (`test_health.py`)

All services respond through the gateway — simple baseline that must pass before anything else runs.

| Check | Endpoint |
|---|---|
| gateway | `GET /health` |
| yallmp | `GET /ai/livez` |
| llogr | `GET /llogr/livez` |
| checkr | `GET /validators/livez` |
| aihub | `GET /ai/hub/livez` |
| typical-agent | `GET /typical-agent/health` |
| annotator-mock | `GET /annotator-mock/health` |
| meta-evaluator | `GET /meta-evaluator/health` |
| langfuse | `GET /api/public/health` |

---

## Scenario 2 · Tracing pipeline (`test_llogr.py`)

Verifies the core path: **ingest → store → forward → appear in Langfuse**.

| Scenario | Steps |
|---|---|
| REST ingestion visible in Langfuse | POST `trace-create` + `span-create` to llogr → poll Langfuse until trace appears with correct observations |
| OTEL ingestion visible in Langfuse | POST protobuf OTLP to `/api/public/otel/v1/traces` → poll Langfuse |
| S3 storage | POST to llogr ingestion → poll MinIO S3 for the expected key under `lf-pk-ai-suite/` |
| Score forwarded | POST score to llogr `/api/public/scores` → verify it appears on the trace in Langfuse |
| Session listing | POST two traces with same session_id → `GET /llogr/api/public/sessions/{session_id}` returns both |

---

## Scenario 3 · Typical-agent end-to-end (`test_typical_agent.py`)

Full black-box: trigger a scenario, wait for completion, verify trace structure in Langfuse.

| Scenario | Steps |
|---|---|
| Single run completes | POST `/typical-agent/api/v0/runs` with `auth-down` → poll until `done` → report has all keys |
| Trace appears in Langfuse | Same run → fetch `trace_url` from report → Langfuse has trace with ≥ 7 observations (classify + 3 spans + diagnose + remediate + evaluate) |
| All scenarios pass | POST `/runs/all` → all 4 complete as `done` (no `error`) |

---

## Scenario 4 · Checkr validation (`test_checkr.py`)

| Scenario | Steps |
|---|---|
| G-eval scores item | POST to `/validators/api/v0/g-eval` with sample input/output → response has `score` between 0–1 |
| Rubric-eval | POST to `/validators/api/v0/rubric-eval` → response has per-criterion scores |
| Async job lifecycle | POST `/api/v0/jobs/validate` → poll `GET /api/v0/jobs/{job_id}` until `done` |

---

## Scenario 5 · Aihub CRUD (`test_aihub.py`)

| Scenario | Steps |
|---|---|
| Leaderboard round-trip | POST a preset to `/projects/{id}/leaderboard` → GET it back → scores match |
| Chat history round-trip | POST an exchange to `/projects/{id}/arena/history` → GET it back → content preserved |
| Projects listing | GET `/projects` → returns at least one project |

---

## Docker Compose integration

```yaml
# docker-compose.yaml addition
integration-tests:
  build:
    context: ./tests
  depends_on:
    langfuse-web:   { condition: service_healthy }
    llogr:          { condition: service_healthy }
    typical-agent:  { condition: service_healthy }
    checkr:         { condition: service_healthy }
    aihub:          { condition: service_healthy }
  environment:
    GATEWAY_URL:          "http://gateway:80"
    LANGFUSE_URL:         "http://langfuse-web:3000"
    LANGFUSE_PUBLIC_KEY:  "lf-pk-ai-suite"
    LANGFUSE_SECRET_KEY:  "lf-sk-ai-suite"
    LLOGR_INGEST_TIMEOUT: "30"
  profiles: ["test"]
  restart: "no"
```

Tests inside the container use `GATEWAY_URL` for everything (single entry point via nginx), plus `LANGFUSE_URL` directly for poll-until-visible assertions.

---

## Folder structure

```
tests/
  conftest.py            # shared fixtures: base URLs, httpx client, poll helper
  test_health.py         # scenario 1
  test_llogr.py          # scenario 2
  test_typical_agent.py  # scenario 3
  test_checkr.py         # scenario 4
  test_aihub.py          # scenario 5
  requirements.txt       # pytest, httpx, pytest-asyncio, opentelemetry-proto
  Dockerfile
```
