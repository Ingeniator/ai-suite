# ai-suite

A local-first toolkit for working with LLMs: proxy, dataset validation, and trace collection — unified behind a single gateway.

## Services

| Service | Description | Gateway route |
|---------|-------------|---------------|
| **yallmp** | LLM proxy with circuit breaker, cost tracking, tracing | `/ai/...` |
| **checkr** | Multi-gate dataset validation with LLM-as-judge | `/validators/...` |
| **llogr** | Langfuse-compatible trace collector with S3 storage | `/llogr/...` |
| **minio** | S3-compatible object storage | Console: `:9001` |
| **gateway** | Nginx reverse proxy — single entrypoint | `:8888` |

## Quick start

```bash
make up        # build and start everything
```

Open [http://localhost:8888](http://localhost:8888) — the index page shows all services with health status and test buttons.

```bash
make down      # stop all
make down-v    # stop all + delete volumes (minio data)
make logs      # follow logs for all services
make ps        # show running services
```

### Start individual services

```bash
make up-yallmp    # proxy only
make up-checkr    # validators only
make up-llogr     # llogr + minio
make up-minio     # minio only
make up-gateway   # nginx gateway only
```

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │             nginx gateway :8888              │
                    │  /ai/* ─► yallmp   /validators/* ─► checkr  │
                    │  /llogr/* ─► llogr    /s3/* ─► minio        │
                    └───────────────────┬──────────────────────────┘
                                        │
          ┌─────────────────────────────┼──────────────────────┐
          │                             │                      │
    ┌─────▼─────┐               ┌───────▼──────┐       ┌──────▼──────┐
    │  yallmp   │──── traces ──►│    llogr     │       │   checkr    │
    │  :5000    │               │    :8000     │       │   :5000     │
    └─────┬─────┘               └───────┬──────┘       └──────┬──────┘
          │                             │                      │
          │ proxy                       │ store                │ LLM-as-judge
          ▼                             ▼                      │
    ┌───────────┐               ┌──────────────┐               │
    │ LM Studio │               │    minio     │               │
    │ :1234     │               │    :9000     │◄──────────────┘
    └───────────┘               └──────────────┘    (via yallmp proxy)
```

## Configuration

### LLM backend

All LLM calls go through yallmp. Point it at any OpenAI-compatible server:

```bash
# yallmp/.env
LLM_PROXY_TARGET_URL=http://host.docker.internal:1234
LLM_PROXY_AUTHORIZATION_TYPE=NONE
```

In docker-compose, `host.docker.internal` resolves to the host machine (Docker Desktop).

### Cost tracking

Edit `yallmp/data/langchain/pricing.json` to add model pricing:

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

Costs appear in the dashboard at `/ai/dashboard`.

### Tracing (yallmp → llogr)

Enabled in docker-compose via environment variables:

```yaml
LLM_TRACING_ENABLED: "true"
LLM_TRACING_BACKEND: "langfuse"
LANGFUSE_HOST: "http://llogr:8000"
LANGFUSE_PUBLIC_KEY: "pk-local"
LANGFUSE_SECRET_KEY: "sk-local"
```

yallmp uses the Langfuse v3 SDK which sends OTEL protobuf spans. llogr accepts both:
- `POST /api/public/ingestion` — legacy Langfuse JSON batch API
- `POST /api/public/otel/v1/traces` — OTLP/HTTP protobuf

### Checkr LLM config

Checkr uses yallmp as a proxy for LLM-as-judge calls. Configure in `checkr/config/llm.yaml`:

```yaml
geval:
  model: llama-3.2-3b-instruct
  api_key: "${GEVAL_API_KEY}"
  api_base: http://yallmp:5000/ai/llm/v1

gabriel:
  model: llama-3.2-3b-instruct
  api_key: "${OPENAI_API_KEY}"
  api_base: http://yallmp:5000/ai/llm/v1
```

This routes all judge calls through yallmp, so they get traced in llogr.

### llogr configuration

llogr uses a YAML config file. The docker-compose setup mounts `llogr/config.gateway.yaml`:

```yaml
s3:
  bucket: "llogr-raw-events"
  region: "us-east-1"
  endpoint: "http://minio:9000"
  public_endpoint: "http://localhost:8888/s3"
  access_key_id: "minioadmin"
  secret_access_key: "minioadmin"

clickbeat:
  api_url: "http://clickbeat:9999/v1/events"
  api_key: "your-key"
  query_url: "http://clickbeat:9999/v1/query"    # optional, for search

server:
  root_path: "/llogr"

features:
  search_enabled: true          # enable full-text search
  search_backend: "duckdb"      # "duckdb", "clickhouse", or "clickbeat"

# only when search_backend: "clickhouse"
clickhouse:
  url: "http://clickhouse:8123"
  database: "default"
  table: "llogr_events"
  user: "default"
  password: ""
```

### Search backends

| Backend | Infra needed | Best for |
|---------|-------------|----------|
| `duckdb` | None (in-process) | Dev/staging, single pod |
| `clickhouse` | ClickHouse instance | Production, multi-pod, high volume |
| `clickbeat` | ClickBeat service | When ClickBeat is already deployed |

**DuckDB** scans S3 files directly using time-range pre-filtering. No index, no extra services.

**ClickHouse** indexes events on ingestion and queries via SQL. Auto-creates the table on startup. Add to docker-compose:

```yaml
clickhouse:
  image: clickhouse/clickhouse-server
  ports:
    - "8123:8123"
  volumes:
    - clickhouse-data:/var/lib/clickhouse
```

**ClickBeat** proxies search queries to an external ClickBeat query API.

### Kafka → ClickHouse (optional)

If you add Kafka to the stack, ClickHouse can consume from it directly using its built-in Kafka engine — no extra services needed.

**1. Add Kafka to docker-compose:**

```yaml
kafka:
  image: bitnami/kafka:latest
  environment:
    KAFKA_CFG_NODE_ID: "1"
    KAFKA_CFG_PROCESS_ROLES: "broker,controller"
    KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: "1@kafka:9093"
    KAFKA_CFG_LISTENERS: "PLAINTEXT://:9092,CONTROLLER://:9093"
    KAFKA_CFG_ADVERTISED_LISTENERS: "PLAINTEXT://kafka:9092"
    KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP: "PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT"
    KAFKA_CFG_CONTROLLER_LISTENER_NAMES: "CONTROLLER"
  volumes:
    - kafka-data:/bitnami/kafka
```

**2. Create a Kafka queue table in ClickHouse:**

```sql
CREATE TABLE kafka_llogr_queue (
    event_id    String,
    event_type  String,
    timestamp   DateTime64(3),
    project_id  String,
    model       String,
    name        String,
    trace_id    String,
    session_id  String,
    body        String
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'llogr-events',
    kafka_group_name = 'clickhouse-llogr',
    kafka_format = 'JSONEachRow';
```

**3. Create a materialized view to sink into the final table:**

```sql
CREATE MATERIALIZED VIEW llogr_events_kafka_mv TO llogr_events AS
SELECT * FROM kafka_llogr_queue;
```

ClickHouse now continuously consumes from the `llogr-events` topic and inserts into `llogr_events`. The Kafka queue table acts as a consumer — the materialized view triggers on each batch and writes to the destination table.

This pattern also works for other sinks (S3 via Kafka Connect, Flink, etc.), but the ClickHouse Kafka engine is the simplest option when ClickHouse is already in the stack.

## UI pages

| URL | Description |
|-----|-------------|
| `http://localhost:8888` | Index page — service table with health and test buttons |
| `http://localhost:8888/ai/dashboard` | yallmp metrics: tokens, cost, latency |
| `http://localhost:8888/ai/docs` | yallmp OpenAPI docs |
| `http://localhost:8888/validators/playground` | checkr validation playground (Pyodide) |
| `http://localhost:8888/validators/docs` | checkr OpenAPI docs |
| `http://localhost:8888/llogr/` | llogr log browser with search |
| `http://localhost:8888/llogr/docs` | llogr OpenAPI docs |
| `http://localhost:9001` | MinIO console (minioadmin/minioadmin) |

## Dataset export

The llogr log browser has an "Export as dataset" button that converts traces into checkr-compatible format:

```json
[
  {
    "messages": [
      {"role": "user", "content": "What is 2+2?"},
      {"role": "assistant", "content": "4."}
    ]
  }
]
```

This file can be uploaded directly to checkr's playground or posted to `/validators/api/v0/validate`.

## Development

Each service has its own Makefile:

```bash
cd yallmp && make run     # run locally
cd checkr && make run     # run locally
cd llogr && make dev  # run locally
```

Run tests:

```bash
cd yallmp && make test
cd checkr && make test
cd llogr && make test
```
