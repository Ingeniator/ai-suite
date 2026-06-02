# Redis Schema — ai-suite

All services share a single Redis instance (`langfuse-redis`, `redis:7-alpine`,
`--maxmemory-policy noeviction`) exposed only inside the Docker Compose network.

---

## Instance topology

| Redis DB | Services                              | Purpose                          |
|----------|---------------------------------------|----------------------------------|
| `0`      | **yallmp** (billing)                  | Billing spend counters           |
| `0`      | **langfuse-web**, **langfuse-worker** | Langfuse internal (queue/cache)  |
| `1`      | **checkr**                            | Async job queue + job records    |
| `1`      | **dataimporter**, **dataimporter-worker** | arq job queue + progress hashes |

> ⚠️ **checkr** and **dataimporter** share DB 1.  
> Key namespacing (`checkr:*` vs `arq:*` / `dataimporter:*`) prevents collisions.

---

## DB 0 — yallmp billing

Env var: `LLM_BILLING_REDIS_URL=redis://langfuse-redis:6379/0`  
Source: `yallmp/app/services/billing.py`, `yallmp/app/middlewares/billing_middleware.py`

### Keys

#### `billing:group:{org}:{period}` — String (float)

Cumulative LLM spend for an organisation in the current billing period.

| Attribute  | Value                                                          |
|------------|----------------------------------------------------------------|
| Type       | String (float stored as text, mutated via `INCRBYFLOAT`)       |
| TTL        | Seconds remaining until end of period (set once on key creation via `EXPIRE … XX=False`) |
| Period key | `"month"` → `YYYY-MM`  \|  `"week"` → `YYYY-WNN`             |

```
billing:group:myorg:2026-05   →  "1.234567"
billing:group:myorg:2026-W21  →  "0.500000"
```

#### `billing:user:{org}/{user_id}:{period}` — String (float)

Cumulative LLM spend for an individual user (sub-key of org) in the current period.

| Attribute  | Value                                                          |
|------------|----------------------------------------------------------------|
| Type       | String (float, via `INCRBYFLOAT`)                              |
| TTL        | Same as the group key (period-end expiry)                      |

```
billing:user:myorg/alice:2026-05  →  "0.420000"
```

### Access patterns

| Operation           | Command(s)                                  | Called from                  |
|---------------------|---------------------------------------------|------------------------------|
| Pre-request check   | `GET billing:group:…`                       | `BillingMiddleware.dispatch`  |
| Charge after call   | `INCRBYFLOAT billing:group:…` + `EXPIRE`    | `billing.charge()`           |
| Charge user         | `INCRBYFLOAT billing:user:…` + `EXPIRE`     | `billing.charge()`           |
| Dashboard (admin)   | `SCAN billing:group:*:{period}`             | `billing.get_billing_summary` |
| Dashboard (user)    | `GET billing:user:{group_id}:{period}`      | `billing.get_billing_summary` |

---

## DB 0 — Langfuse (langfuse-web / langfuse-worker)

Env vars: `REDIS_HOST=langfuse-redis`, `REDIS_PORT=6379` (no DB → defaults to `0`)

Langfuse manages its own key schema internally (BullMQ job queues, cache, rate-limit
counters). The exact keys are defined by the upstream Langfuse v3 image and are not
owned by this codebase. Do not write to DB 0 except through the `LLM_BILLING_REDIS_URL`
connection.

---

## DB 1 — checkr job queue

Env var: `CHECKR_REDIS_URL=redis://langfuse-redis:6379/1`  
Source: `checkr/services/job_service.py`, `checkr/core/config.py`, `checkr/services/job_worker.py`

### Keys

#### `checkr:jobs:{job_id}` — String (JSON)

Full serialised `JobRecord` for a single validation job.

| Attribute  | Value                                                              |
|------------|--------------------------------------------------------------------|
| Type       | String — JSON blob (`pydantic` model, `decode_responses=True`)    |
| TTL        | `CHECKR_JOB_TTL` seconds (default **86 400 s / 24 h**)            |
| Key prefix | configurable via `CHECKR_JOB_KEY_PREFIX` (default `checkr:jobs:`) |

**JSON schema** (`JobRecord`):

```jsonc
{
  "job_id":       "550e8400-e29b-41d4-a716-446655440000",  // UUID4
  "status":       "queued" | "running" | "completed" | "failed" | "cancelled",
  "gates":        ["gate-name-1", "gate-name-2"],
  "dataset_size": 1000,
  "progress": {                          // per-gate progress counters
    "gate-name-1": { "current": 42, "total": 100 },
    "gate-name-2": { "current": 0,  "total": 100 }
  },
  "created_at":   "2026-05-27T10:00:00+00:00",  // ISO-8601 UTC
  "started_at":   "2026-05-27T10:00:01+00:00" | null,
  "completed_at": "2026-05-27T10:02:00+00:00" | null,
  "result":       { /* gate-level results dict */ } | null,
  "error":        "error message string" | null
}
```

#### `checkr:queue` — List (FIFO queue)

Pending job payloads consumed by the background worker via `BLPOP`.

| Attribute  | Value                                        |
|------------|----------------------------------------------|
| Type       | List                                         |
| TTL        | None (persists until consumed or Redis flush) |
| Direction  | Producer: `RPUSH` — Consumer: `BLPOP` (left) |
| Key        | configurable via `CHECKR_JOB_QUEUE_KEY` (default `checkr:queue`) |

**Element JSON schema**:

```jsonc
{
  "job_id":  "550e8400-e29b-41d4-a716-446655440000",
  "dataset": [ /* array of dataset items */ ],
  "options": { /* gate execution options dict */ }
}
```

### Access patterns

| Operation        | Command(s)                        | Called from                   |
|------------------|-----------------------------------|-------------------------------|
| Create job       | `SET checkr:jobs:{id} <json> EX`  | `JobService.create_job`        |
| Read job         | `GET checkr:jobs:{id}`            | `JobService.get_job`           |
| Update job       | `SET checkr:jobs:{id} <json> EX`  | `JobService.update_job`        |
| Progress update  | `GET` + `SET checkr:jobs:{id} EX` | `JobService.update_progress`   |
| Enqueue          | `RPUSH checkr:queue <payload>`    | `JobService.enqueue_job`       |
| Worker dequeue   | `BLPOP checkr:queue 5` (timeout)  | `job_worker.worker_loop`       |

---

## DB 1 — dataimporter job queue (arq)

Env var (via `config.gateway.yaml`): `redis_url: redis://langfuse-redis:6379/1`  
Source: `dataimporter/src/dataimporter/queue.py`, `dataimporter/src/dataimporter/worker.py`,
`dataimporter/src/dataimporter/routes/export.py`

### arq-managed keys (internal)

[arq](https://arq-docs.helpmanual.io/) owns these keys. Their prefixes are fixed by the library.

| Key pattern            | Type           | Description                           |
|------------------------|----------------|---------------------------------------|
| `arq:queue`            | Sorted Set     | Pending jobs (score = scheduled time) |
| `arq:job:{job_id}`     | Hash           | Job metadata & serialised arguments   |
| `arq:result:{job_id}`  | String (bytes) | Serialised job return value           |
| `arq:in-progress`      | Sorted Set     | Jobs currently running                |
| `arq:abort:{job_id}`   | String         | Abort signal written by cancellation  |

arq sets its own TTLs (default: results kept for ~1 day).

### Application-managed keys

#### `dataimporter:progress:{job_id}` — Hash

Real-time per-file import progress, written by the worker and read by the status endpoint.

| Attribute | Value                                                         |
|-----------|---------------------------------------------------------------|
| Type      | Hash                                                          |
| TTL       | **3 600 s (1 h)**, refreshed on every progress update         |

**Fields**:

| Field         | Type    | Description                              |
|---------------|---------|------------------------------------------|
| `files_done`  | integer | Number of files/events processed so far  |
| `files_total` | integer | Total files/events in the job            |
| `bytes_done`  | integer | Bytes transferred so far                 |

```
HGETALL dataimporter:progress:abc123
→ files_done  42
→ files_total 100
→ bytes_done  4718592
```

### Registered arq tasks

| Function name           | Triggered by                             | Description              |
|-------------------------|------------------------------------------|--------------------------|
| `import_dataset`        | `POST /api/public/export/dataset`        | S3-to-dataset file import |
| `import_dataset_events` | `POST /api/public/export/dataset/events` | In-memory events export  |

Worker config: `max_jobs=1`, `job_timeout=3600 s`.

### Access patterns

| Operation       | Command(s)                                            | Called from                          |
|-----------------|-------------------------------------------------------|--------------------------------------|
| Enqueue job     | arq `enqueue_job()` → `ZADD arq:queue`                | `routes/export.py`                   |
| Update progress | `HSET dataimporter:progress:{id} …` + `EXPIRE … 3600` | `worker.py _set_progress`            |
| Read progress   | `HGETALL dataimporter:progress:{id}`                  | `routes/export.py get_export_status` |
| Poll job status | arq `Job.status()` → `HGET arq:job:{id}`              | `routes/export.py get_export_status` |
| Fetch result    | arq `Job.result()` → `GET arq:result:{id}`            | `routes/export.py get_export_status` |

---

## Key namespace summary

```
DB 0
├── billing:group:{org}:{YYYY-MM|YYYY-WNN}      # yallmp — float string
├── billing:user:{org}/{user}:{period}           # yallmp — float string
└── <langfuse internal keys>                     # managed by langfuse image

DB 1
├── checkr:jobs:{uuid}                           # checkr — JSON string, TTL 24h
├── checkr:queue                                 # checkr — List (BLPOP queue)
├── arq:queue                                    # dataimporter/arq — Sorted Set
├── arq:job:{uuid}                               # dataimporter/arq — Hash
├── arq:result:{uuid}                            # dataimporter/arq — String
├── arq:in-progress                              # dataimporter/arq — Sorted Set
├── arq:abort:{uuid}                             # dataimporter/arq — String
└── dataimporter:progress:{uuid}                 # dataimporter — Hash, TTL 1h
```

---

## Configuration reference

| Service             | Env var / config key        | Value in docker-compose / default         |
|---------------------|-----------------------------|-------------------------------------------|
| yallmp              | `LLM_BILLING_REDIS_URL`     | `redis://langfuse-redis:6379/0`           |
| checkr              | `CHECKR_REDIS_URL`          | `redis://langfuse-redis:6379/1`           |
| checkr              | `CHECKR_JOB_TTL`            | `86400` (seconds)                         |
| checkr              | `CHECKR_JOB_QUEUE_KEY`      | `checkr:queue`                            |
| checkr              | `CHECKR_JOB_KEY_PREFIX`     | `checkr:jobs:`                            |
| dataimporter        | `server.redis_url`          | `redis://langfuse-redis:6379/1` (config.yaml) |
| langfuse-web/worker | `REDIS_HOST` / `REDIS_PORT` | `langfuse-redis` / `6379` (DB 0)         |
