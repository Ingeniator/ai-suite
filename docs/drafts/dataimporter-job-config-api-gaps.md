# Dataimporter — Job Config API & UI Gaps

Current state: the API can validate a JobConfig YAML (`POST /api/public/config/validate`)
but has no way to submit one and run it, and no scheduling support at all.

---

## API Gaps

### 1. Run a job from config

**Missing:** an endpoint that accepts a JobConfig (the YAML we designed) and triggers an export.

The existing `POST /api/public/export/dataset` takes raw low-level parameters
(`target`, `datasource`, `keys`, `dataset_name`, …) — it does not accept a JobConfig.

**Proposed:**
```
POST /api/public/jobs/run
Body: JobConfig (YAML or JSON)
Response: { job_id }
```

This endpoint should:
- validate the config (reuse existing validate logic)
- resolve the datasource, apply filters / masking / sampling
- resolve asset references if `asset_resolution.enabled`
- export to the destination
- fire the webhook on completion (if configured)
- return a `job_id` to poll for status

---

### 2. Job status & history

**Missing:** a way to list all jobs and their statuses.

`GET /api/public/export/status/{job_id}` exists but requires knowing the `job_id` upfront.

**Proposed:**
```
GET /api/public/jobs               — list jobs (status, created_at, datasource, destination)
GET /api/public/jobs/{job_id}      — same as existing status endpoint (alias or replace)
```

---

### 3. Scheduled jobs — CRUD

**Missing:** everything. The `schedule` block in JobConfig has no backing API.

**Proposed:**
```
POST   /api/public/jobs/scheduled          — create a scheduled job from a JobConfig
GET    /api/public/jobs/scheduled          — list all scheduled jobs
GET    /api/public/jobs/scheduled/{id}     — get a single scheduled job + its run history
PATCH  /api/public/jobs/scheduled/{id}     — update config or toggle enabled/disabled
DELETE /api/public/jobs/scheduled/{id}     — delete a scheduled job
```

A scheduled job stores the full JobConfig and fires `jobs/run` logic on the cron schedule.
The `schedule.enabled` flag in the config maps to pausing without deletion.

---

### 4. Config storage

**Missing:** there is no persistence layer for JobConfig documents.

To support scheduled jobs and job history, configs need to be stored somewhere
(DB table, S3 object, Redis key). Decision needed before implementation:
- where configs are stored
- whether one-shot run configs are stored (for audit) or discarded after execution

---

## UI Gaps — FIXED

The following gaps have been resolved in `browser.html`:

| Gap | What was fixed |
|---|---|
| §5 Masking rules | Added field masking rule builder (field path + action + max_length for truncate). Serializes/deserializes `masking.rules[]`. |
| §6 Asset resolution | Expanded checkbox into a full panel: source fields (tag list), fetch mode dropdown, check_availability toggle. Serializes all into `asset_resolution`. |
| §7 Destination | Added Destination section: target dropdown (populated from server targets), dataset_name, access, dataset_type. Serializes into `destination`. |
| §8 Webhook | Added Webhook section: URL, headers (key: value textarea), secret, timeout. Serializes into `webhook`. |
| §8 Schedule | Added Schedule section: cron expression, timezone, enabled toggle. Serializes into `schedule`. |
| §9 `in`/`not_in` operators | Added to filter operator dropdown with comma-separated value input. Values split to arrays on YAML export, joined on YAML import. |
| §10 Filter mode `and`/`or` | Added AND/OR toggle in filter panel header. Serializes as `filters.mode`. |
