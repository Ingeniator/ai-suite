# Billing

Org-level spend tracking with per-user limits, real-time enforcement, and a ClickHouse-backed audit ledger.

---

## Architecture

```
Request → nginx → yallmp
                    │
                    ├─ [pre-request]   Redis: check group/user spend vs limit → 429 if over
                    ├─ [proxy]         LLM upstream
                    └─ [post-request]  Redis: INCRBYFLOAT actual cost
                                       llogr: trace ingested (generation-create event)
                                                └─ ClickHouse: llogr_events  ← source of truth
                                                └─ langfuse-web: forwarded (fan-out)

yallmp (startup + every 5 min)
  └─ GET /llogr/api/public/billing/summary  ← seeds Redis from ClickHouse
       └─ Redis: SET ... GT (only raises, never lowers)
```

### Component responsibilities

| Concern | Component | Notes |
|---|---|---|
| **Billing source of truth** | llogr ClickHouse (`llogr_events`) | Exact `SUM(cost)` per org/user per period |
| **Real-time enforcement** | yallmp + Redis | Hot-path check, seeded from llogr |
| **Operational metrics** | Victoria Metrics | `llm_cost_total`, token counters — approximate, for trends only |
| **Billing dashboard** | llogr `/llogr/billing` | ClickHouse-backed spend view |
| **Metrics dashboard** | yallmp `/ai/dashboard` | Victoria Metrics-backed operational view |
| **Per-request audit log** | llogr ClickHouse | 90-day TTL, exportable as JSONL |

---

## Two Dashboards

The system has two separate dashboard surfaces with different backends and purposes:

### yallmp dashboard — `/ai/dashboard`

- **Backend:** Prometheus / Victoria Metrics
- **Refresh:** real-time (scrape interval ~15 s)
- **Shows:** token usage by model/team, LLM cost trends (time-series), HTTP request rates, search cost, session list
- **Billing section:** links out to llogr billing page — not rendered inline

Best for: operational monitoring, latency/error rates, real-time cost trends.

### llogr billing page — `/llogr/billing`

- **Backend:** ClickHouse (`llogr_events`)
- **Refresh:** manual / period picker
- **Shows:** per-org group spend (total / input / output / request count), per-user breakdown (paginated), caller's own spend
- **Period picker:** current month, last month, or custom ISO 8601 range

Best for: accounting, spend reconciliation, audit, user-level cost attribution.

| | yallmp dashboard | llogr billing |
|---|---|---|
| Data freshness | Real-time | Sync interval (default 5 min) |
| Historical depth | VM retention policy | ClickHouse TTL (90 days) |
| Accuracy | Approximate (`increase()` interpolation) | Exact (event log `SUM`) |
| Limit metadata | Yes (`limits.yaml`) | No |
| Audit / export | No | Yes (JSONL export) |
| URL | `/ai/dashboard` | `/llogr/billing` |

---

## Redis Enforcement Cache

Redis holds running spend totals used for sub-millisecond pre-request checks. It is **not** the source of truth — it is a cache seeded from ClickHouse.

### Key schema

```
billing:group:{org}:{period}       # e.g. billing:group:acme:2026-06
billing:user:{group_id}:{period}   # e.g. billing:user:acme/alice:2026-06
```

Period format: `YYYY-MM` (monthly) or `YYYY-WNN` (weekly). Keys carry a TTL set to the end of the current period.

### Sync behaviour

On yallmp startup and every `LLM_BILLING_SYNC_INTERVAL` seconds:

1. Call `GET /llogr/api/public/billing/summary` with `X-Role: SUPER_ADMIN`
2. Paginate through all groups and users (`user_limit=500`)
3. Write each counter to Redis using `SET key value EX ttl GT`

`SET ... GT` (Redis 7+) means the sync only **raises** a counter — in-flight `INCRBYFLOAT` increments that happened between the last ClickHouse commit and the sync are preserved, not overwritten.

### Enforcement rules

- **Group limit reached:** returns `429` with `{"error": "group spend limit reached"}`
- **User limit reached:** returns `429` with `{"error": "user spend limit reached"}`
- **Alert threshold (default 80%):** adds `X-Billing-Warning: approaching group limit` response header
- **Redis down:** fails open — LLM access takes priority over billing checks

---

## Tier Configuration

`yallmp/data/billing/limits.yaml` — loaded on startup, reload requires restart.

```yaml
tiers:
  tier1:
    period: month       # or "week"
    group_limit: 100.0  # USD per period for the whole org
    user_limit: 10.0    # USD per period per user
    alert_threshold: 0.8

  tier_enterprise:
    period: month
    group_limit: 1000.0
    user_limit: 50.0
    alert_threshold: 0.9

orgs:
  acme: tier_enterprise
  default: tier1
  unknown: tier1
```

---

## API Reference

### Billing summary (source of truth)

```
GET /llogr/api/public/billing/summary
Headers: X-Group-ID, X-Role

Query params:
  period=YYYY-MM          # full calendar month (default: current month)
  start=ISO8601&end=ISO8601  # custom range (mutually exclusive with period)
  user_limit=500          # users per page
  user_offset=0           # pagination offset

Response:
{
  "period": "2026-06",
  "groups": [
    {"org": "acme", "group_spent": 42.32, "input_spent": 19.1, "output_spent": 23.2, "request_count": 1500}
  ],
  "users": [
    {"project_id": "acme/alice", "user_spent": 8.75, "input_spent": 4.0, "output_spent": 4.75, "request_count": 350}
  ],
  "has_more": false,
  "current_user": {"project_id": "acme/alice", "user_spent": 8.75, ...}
}
```

Full parameter and schema reference: `llogr/doc/billing.swagger.yaml`

### Caller identity (used by the billing page)

```
GET /llogr/api/public/billing/who
Headers: X-Group-ID, X-Role

Response:
{"role": "ORG_ADMIN", "project_id": "acme/alice", "org": "acme"}
```

### Trace export (billing audit)

```
GET /llogr/api/public/export?start=2026-06-01T00:00:00&end=2026-06-30T23:59:59
Authorization: Basic {base64(group_id:group_id)}

Response: application/x-ndjson (streaming JSONL)
Each line: {"event_id": "...", "timestamp": "...", "project_id": "...", "model": "...", "body": {...}}
```

---

## Configuration

### yallmp environment variables

```bash
LLM_BILLING_ENABLED=true
LLM_BILLING_REDIS_URL=redis://langfuse-redis:6379/0
LLM_BILLING_LIMITS_PATH=data/billing/limits.yaml

# llogr URL for Redis cache seeding (falls back to LLM_TRACING_HOST if empty)
LLM_BILLING_SYNC_URL=http://llogr:5000
LLM_BILLING_SYNC_INTERVAL=300          # seconds between syncs
```

---

## Key Files

| File | Purpose |
|---|---|
| `yallmp/app/services/billing.py` | Limits loader, `charge()`, `get_billing_summary()` |
| `yallmp/app/services/billing_sync.py` | Redis cache sync from llogr |
| `yallmp/app/middlewares/billing_middleware.py` | Pre-request limit enforcement |
| `yallmp/data/billing/limits.yaml` | Tier and org configuration |
| `llogr/src/llogr/routes/billing.py` | Summary API, who endpoint, billing page route |
| `llogr/src/llogr/clickhouse.py` | `get_billing_summary_ch()` — ClickHouse aggregation |
| `llogr/src/llogr/templates/billing.html` | Billing dashboard page |
| `llogr/doc/billing.swagger.yaml` | Full OpenAPI spec for billing endpoints |
