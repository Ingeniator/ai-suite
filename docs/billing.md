# Billing Service

Internal org billing with staff visibility: usage tracking, spend limits, audit trail, and trace export as dataset.

---

## Architecture

No new services or databases. All functionality uses existing infrastructure.

```
Request → nginx → yallmp
                    │
                    ├─ [pre-request]  Redis: check group/user spend vs limit → 429 if over
                    ├─ [proxy]        LLM upstream
                    └─ [post-request] Redis: increment counters with actual cost
                                      llogr: trace ingested (generation-create event)
                                        └─ ClickHouse: llogr_events (billing audit log)
                                        └─ langfuse-web: forwarded for UI (fan-out)
```

| Concern | Component |
|---|---|
| Time-series cost dashboard | Victoria Metrics — PromQL on `llm_cost_total`, `llm_*_token_usage_total` |
| Per-request audit log + dataset export | llogr `llogr_events` (ClickHouse), `event_type = 'generation-create'` |
| Real-time limit enforcement | Redis — `billing:group:{org}:{period}` / `billing:user:{group_id}:{period}` |
| Billing UI | yallmp dashboard — Billing & Limits tab |

---

## Design Decisions

### 1. Storage
- **Victoria Metrics** for aggregated time-series (cost trends, token usage)
- **llogr ClickHouse** (`llogr_events`) as the per-request billing record — no separate `billing_events` table needed; generation events already contain model, tokens, cost, group_id, input, output
- **Redis** for real-time running totals used in hot-path limit checks

### 2. llogr as fan-out proxy
yallmp sends traces to llogr (not directly to langfuse-web). llogr stores to ClickHouse and forwards to Langfuse fire-and-forget. Langfuse becomes a consumer, not the source of truth.

```yaml
# llogr/config.gateway.yaml
features:
  forward:
    - url: "http://langfuse-web:3000"
      pass_auth: true   # preserves per-group Langfuse project association
```

### 3. Limits
Tier-based config in `yallmp/data/billing/limits.yaml`. Two-level hierarchy: `{org}/{user_id}` encoded in `group_id`.

```yaml
tiers:
  tier1:
    period: month          # or "week"
    group_limit: 100.0     # USD per period for the whole org
    user_limit: 10.0       # USD per period per user
    alert_threshold: 0.8   # soft warning at 80%

orgs:
  my-org: tier1
  default: tier1
  unknown: tier1
```

Enforcement:
- **Pre-request**: middleware reads Redis total, returns 429 if already at limit (one-request overage accepted)
- **Post-request**: proxy handler increments Redis counters with actual cost after response
- **Soft warning**: `X-Billing-Warning: approaching group limit` response header
- **Redis down**: fail open — LLM access takes priority

### 4. Trace export
`GET /llogr/api/public/export?start=...&end=...` streams `generation-create` events from ClickHouse as JSONL. Auth-scoped: users export their own group, org admins export the full org prefix.

### 5. UI
Billing tab in yallmp dashboard (`/ai/dashboard`) shows:
- Group spend gauges (spent / limit with progress bar)
- User spend gauge
- Download link → llogr export for current period

---

## Files Changed

| File | Change |
|---|---|
| `llogr/src/llogr/config.py` | Added `ForwardTargetConfig`, `forward` field to `FeaturesConfig` |
| `llogr/src/llogr/forward.py` | **New** — HTTP fan-out with auth pass-through |
| `llogr/src/llogr/processing.py` | Fire-and-forget forward tasks after storage |
| `llogr/src/llogr/clickhouse.py` | Added `export_generations_ch()` streaming function |
| `llogr/src/llogr/routes/export.py` | **New** — streaming JSONL export endpoint |
| `llogr/src/llogr/main.py` | Registered export router |
| `llogr/config.gateway.yaml` | Added `features.forward` section |
| `yallmp/app/core/config.py` | Added `billing_enabled`, `billing_redis_url`, `billing_limits_path` |
| `yallmp/app/services/billing.py` | **New** — limits loader, `charge()`, `get_billing_summary()` |
| `yallmp/app/middlewares/billing_middleware.py` | **New** — pre-request limit check |
| `yallmp/app/core/app.py` | Redis + limits lifespan, `BillingMiddleware`, `/dashboard/api/billing` |
| `yallmp/app/core/proxy.py` | `asyncio.create_task(charge(...))` after cost calc (streaming + non-streaming) |
| `yallmp/app/templates/dashboard.html` | Billing & Limits tab with gauges and export link |
| `yallmp/data/billing/limits.yaml` | **New** — tier/org config |
| `yallmp/pyproject.toml` | Added `redis[hiredis]>=5.0` |
| `docker-compose.yaml` | `LLM_TRACING_HOST` → llogr, added `LLM_BILLING_*`, updated `depends_on` |

---

## Configuration

### Enable billing in docker-compose

```yaml
# yallmp service environment
LLM_BILLING_ENABLED: "true"
LLM_BILLING_REDIS_URL: "redis://langfuse-redis:6379/0"
LLM_BILLING_LIMITS_PATH: "data/billing/limits.yaml"  # default, optional
```

### Add a new org tier

Edit `yallmp/data/billing/limits.yaml` and restart yallmp (limits loaded on startup):

```yaml
tiers:
  tier_enterprise:
    period: month
    group_limit: 1000.0
    user_limit: 50.0
    alert_threshold: 0.9

orgs:
  my-new-org: tier_enterprise
```

---

## API Reference

### Billing dashboard data
```
GET /ai/dashboard/api/billing
Headers: x-group-id, x-role

Response:
{
  "period": "2026-05",
  "groups": [{"org": "...", "group_limit": 100.0, "group_spent": 42.5, "group_pct": 42.5, "alert": false}],
  "user": {"group_id": "...", "user_limit": 10.0, "user_spent": 5.2, "user_pct": 52.0}
}
```

### Trace export
```
GET /llogr/api/public/export?start=2026-05-01T00:00:00&end=2026-05-19T12:00:00
Authorization: Basic {base64(group_id:group_id)}

Response: application/x-ndjson (streaming JSONL)
Each line: {"event_id": "...", "timestamp": "...", "project_id": "...", "model": "...", "body": {...}}
```

---

## Redis Key Schema

```
billing:group:{org}:{period}        # e.g. billing:group:my-org:2026-05
billing:user:{group_id}:{period}    # e.g. billing:user:my-org/alice:2026-05
```

Period format: `YYYY-MM` (monthly) or `YYYY-WNN` (weekly). Keys have TTL set to end of period.

---

## Verification

1. **Fan-out**: send a request via yallmp → check `SELECT count() FROM default.llogr_events WHERE event_type='generation-create'` in ClickHouse → confirm trace appears in Langfuse UI
2. **Limits**: set a low `group_limit` (e.g. `0.001`) for a test org → send requests → confirm 429 returned → check Redis key with `redis-cli GET "billing:group:my-org:$(date +%Y-%m)"`
3. **Export**: `curl -u "my-org/alice:my-org/alice" "http://localhost:8888/llogr/api/public/export?start=2026-05-01T00:00:00&end=2026-05-19T23:59:59"` → verify JSONL output
4. **Dashboard**: open `/ai/dashboard` → Billing & Limits section shows gauges and export link
