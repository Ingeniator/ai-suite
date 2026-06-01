# ai-suite

A local-first platform for running, evaluating, and continuously improving LLM systems.
Unified behind a single nginx gateway on `:8888`.

---

## Services

### Tier 1 — Traffic & Trace Capture

| Service | Description | Gateway route |
|---------|-------------|---------------|
| **yallmp** | LLM proxy — OpenAI-compatible, cost tracking, circuit breaker, tracing | `/ai/...` |
| **llogr** | Langfuse-compatible trace store — S3 (minio) + ClickHouse + search | `/llogr/...` |
| **langfuse** | Trace UI, score visualisation, dataset management | `:3031` (direct) |
| **clickstream** | User-action event stream for human quality signals | internal |
| **minio** | S3-compatible object store for raw trace JSONL and media | Console `:9002` |

### Tier 2 — Automated Evaluation

| Service | Description | Gateway route |
|---------|-------------|---------------|
| **checkr** | Multi-gate dataset/trace validator — G-Eval, GABRIEL, LLM-as-judge | `/validators/...` |
| **typical-agent** | Demo SRE agent that evaluates its own output quality | `/typical-agent/...` |

### Tier 3 — Dataset Curation

| Service | Description | Gateway route |
|---------|-------------|---------------|
| **dataimporter** | Browse S3/ClickHouse/langfuse, sample scored traces, import to dataset services | `/dataimporter/...` |
| **dataset-mock** | Local mock dataset service (target for dataimporter in dev/test) | `/dataset-mock/...` |

### Tier 4 — CEFS Annotation Loop

| Service | Description | Gateway route |
|---------|-------------|---------------|
| **annotator-mock** | Annotation mock service — simulates 3 annotators labelling each trace | `/annotator-mock/...` |
| **annotator-tui** | Live TUI overseer for annotator-mock (projects / tasks / results) | `/annotator-tui/` |
| **meta-evaluator** | CEFS orchestrator — bridges dataset-mock → annotator-mock, computes AI evaluator quality metrics | `/meta-evaluator/...` |

### Tier 5 — Observability

| Service | Description | Port |
|---------|-------------|------|
| **aihub** | Leaderboard + chat history — FastAPI + PostgreSQL | `/ai/hub/...` |
| **prometheus** | Metrics collection from all services | `:9090` |
| **grafana** | Dashboards for latency, cost, evaluator quality, CEFS metrics | `:3000` |

---

## Quick start

```bash
docker compose up -d
```

Open **http://localhost:8888** — the index page shows all services with live health dots and one-click test buttons.

```bash
docker compose down          # stop
docker compose down -v       # stop + delete all volumes
docker compose logs -f       # follow all logs
docker compose ps            # show status
```

### Start a subset

```bash
docker compose up -d yallmp llogr minio gateway
docker compose up -d annotator-mock meta-evaluator dataset-mock
```

---

## Architecture

```
LLM apps / agents
      │ OpenAI-compatible API calls
      ▼
┌─────────────────────────────────────────────────────────────┐
│                   nginx gateway  :8888                       │
│   /ai/*        ──► yallmp         /llogr/*   ──► llogr      │
│   /validators/* ──► checkr        /ai/hub/*  ──► aihub      │
│   /dataimporter/* ──► dataimporter  /s3/*    ──► minio       │
│   /annotator-mock/* ──► annotator-mock                       │
│   /annotator-tui/*  ──► annotator-tui (WebSocket/ttyd)       │
│   /meta-evaluator/* ──► meta-evaluator                       │
│   /typical-agent/*  ──► typical-agent                        │
│   /dataset-mock/*   ──► dataset-mock                         │
└─────────────────────────────────────────────────────────────┘
      │                │                   │
      ▼                ▼                   ▼
  yallmp           llogr              checkr
  (proxy)     (trace store)       (evaluator)
      │          │     │               │
      │          │  ClickHouse      langfuse
      │          │  (indexed)       (trace UI)
      │       minio
      │       (raw S3)
      │
      ▼
  LM Studio / any OpenAI-compatible backend  :1234

─── CEFS annotation loop ────────────────────────────────────
  dataimporter ──► dataset-mock ──► meta-evaluator
                                        │
                              annotator-mock (3 simulated annotators)
                                        │
                              metrics: accuracy / κ / precision / recall
                                        │
                              prometheus ──► grafana
```

---

## CEFS — Continuous Evaluator Refinement System

CEFS is the feedback loop that validates and improves the LLM evaluators themselves.

```
① Production traffic → yallmp → llogr (all traces captured)
② checkr evaluates all traces async (PASS / FAIL + score)
③ dataimporter samples borderline/disagreement cases → dataset-mock
④ meta-evaluator picks up dataset → annotator-mock (human labels)
⑤ meta-evaluator computes: agreement rate, accuracy, Cohen's κ
⑥ metrics → prometheus / grafana / aihub leaderboard
⑦ low accuracy → refine evaluator → back to ②
```

See [`docs/cefs.md`](docs/cefs.md) for the full design.

### Try the full CEFS loop

Use the **"Full CEFS run"** test button on the dashboard, or from the CLI:

```bash
# 1. push some scored traces into dataset-mock
DS=$(curl -s -X POST http://localhost:8888/dataset-mock/api/v0/datasets \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer any' \
  -d '{"name":"my-batch","access":"organization","dataset_type":"DATASET"}' | jq -r .id)

printf '{"trace_id":"t1","score":0.91,"verdict":"PASS","llm_output":"Paris is the capital"}\n
{"trace_id":"t2","score":0.32,"verdict":"FAIL","llm_output":"The sky is green"}' > /tmp/traces.jsonl

curl -s -X POST "http://localhost:8888/dataset-mock/api/v0/datasets/$DS/files" \
  -H 'Authorization: Bearer any' \
  -F "file=@/tmp/traces.jsonl;type=application/json"

# 2. trigger CEFS run (or wait up to 60s for the auto-poller)
RUN=$(curl -s -X POST http://localhost:8888/meta-evaluator/api/v0/runs \
  -H 'Content-Type: application/json' \
  -d "{\"dataset_id\":\"$DS\",\"dataset_name\":\"my-batch\"}" | jq -r .id)

# 3. poll until done
until [ "$(curl -s http://localhost:8888/meta-evaluator/api/v0/runs/$RUN | jq -r .state)" = "DONE" ]; do
  sleep 1; echo "waiting..."
done

# 4. see results
curl -s http://localhost:8888/meta-evaluator/api/v0/runs/$RUN | jq .metrics
curl -s http://localhost:8888/meta-evaluator/api/v0/summary
```

---

## Configuration

### LLM backend

All LLM calls go through yallmp. Point it at any OpenAI-compatible server:

```bash
# yallmp/.env
LLM_PROXY_TARGET_URL=http://host.docker.internal:1234
LLM_PROXY_AUTHORIZATION_TYPE=NONE
```

`host.docker.internal` resolves to the host machine under Docker Desktop.

### Cost tracking

Edit `yallmp/data/langchain/pricing.json`:

```json
{
  "prefix": "localllm",
  "currency": "USD",
  "pricing": {
    "llama-3.2-3b-instruct": {
      "input_cost_per_token": 0.00006,
      "output_cost_per_token": 0.00006
    }
  }
}
```

Costs appear at `/ai/dashboard`.

### Checkr LLM config

Checkr routes judge calls through yallmp so they are traced. Configure in `checkr/config/llm.yaml`:

```yaml
geval:
  model: llama-3.2-3b-instruct
  api_base: http://yallmp:5000/ai/llm/v1
```

### llogr search backend

| Backend | Infra | Best for |
|---------|-------|----------|
| `duckdb` | None (in-process) | Dev / single pod |
| `clickhouse` | ClickHouse instance | Production, high volume |

Configure in `llogr/config.gateway.yaml`.

---

## UI pages

| URL | Description |
|-----|-------------|
| `http://localhost:8888` | Dashboard — all services, health, test buttons |
| `http://localhost:8888/ai/dashboard` | yallmp — tokens, cost, latency |
| `http://localhost:8888/ai/docs` | yallmp OpenAPI |
| `http://localhost:8888/validators/playground` | checkr validation playground |
| `http://localhost:8888/validators/docs` | checkr OpenAPI |
| `http://localhost:8888/llogr/` | llogr trace browser |
| `http://localhost:8888/llogr/docs` | llogr OpenAPI |
| `http://localhost:8888/dataimporter/` | dataimporter UI |
| `http://localhost:8888/ai/hub/` | aihub leaderboard |
| `http://localhost:8888/annotator-mock/docs` | annotator-mock OpenAPI |
| `http://localhost:8888/annotator-tui/` | annotator-mock live TUI (browser terminal) |
| `http://localhost:8888/meta-evaluator/docs` | meta-evaluator OpenAPI |
| `http://localhost:8888/meta-evaluator/api/v0/summary` | CEFS aggregate metrics |
| `http://localhost:8888/typical-agent/docs` | typical-agent OpenAPI |
| `http://localhost:3000` | Grafana dashboards |
| `http://localhost:3031` | Langfuse trace UI |
| `http://localhost:9002` | MinIO console (`minioadmin` / `minioadmin`) |
| `http://localhost:9090` | Prometheus |

---

## Service READMEs

Each service has its own README with API docs, env vars, and examples:

- [`yallmp/README.md`](yallmp/README.md)
- [`checkr/README.md`](checkr/README.md)
- [`llogr/README.md`](llogr/README.md)
- [`dataimporter/README.md`](dataimporter/README.md)
- [`aihub/README.md`](aihub/README.md)
- [`annotator-mock/README.md`](annotator-mock/README.md) — annotator-mock + annotator-tui
- [`meta-evaluator/README.md`](meta-evaluator/README.md) — CEFS orchestrator
- [`docs/cefs.md`](docs/cefs.md) — full CEFS design and roadmap
