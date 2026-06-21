# CEFS Orchestration

CEFS should use Airflow as the outer workflow orchestrator, while keeping CEFS
domain logic inside the existing services. Airflow should schedule, retry,
observe, and rerun workflows. It should not become the place where evaluator
semantics, sampling rules, annotation rules, or promotion decisions live.

---

## Recommendation

Use **one generic Airflow DAG** for the regular CEFS loop, parameterized by
project, agent, datasource, evaluator profile, sampling policy, and time window.

Do **not** create one DAG per agent. New agents, datasources, or projects should
be added as registry rows/config records, not as new DAG files.

Airflow DAG code should stay thin:

1. Load active CEFS pipelines from the registry.
2. Dynamically map a task group over those pipelines.
3. Call service APIs with explicit IDs and time windows.
4. Persist run IDs and status transitions in the CEFS state store.
5. Fail loudly when a service contract is broken.

---

## Why Airflow

Airflow already provides the operational machinery CEFS needs:

- schedules and manual reruns
- retries and retry policies
- task dependency graph
- backfills over historical windows
- run history and logs
- branching based on metrics
- operational UI for failed steps

A custom "CEFS orchestrator service" would quickly need to rebuild those
features. It may still be useful to have a small CEFS registry/state API, but
that API should own durable domain state, not workflow execution.

---

## Service Boundary

| Component | Owns |
|---|---|
| **Airflow** | Cadence, task dependencies, retries, backfills, manual reruns |
| **checkr** | Evaluator execution and gate results |
| **dataimporter** | Trace search, filtering, sampling, masking, export |
| **databridge** | Connection and credential management for sources/sinks |
| **meta-evaluator** | Annotation task orchestration and evaluator quality metrics |
| **dta-annotator / annotator-mock** | Human labeling workflow |
| **CEFS registry/state API** | Active pipelines, evaluator versions, provenance, run state, promotion decisions |

Airflow should call APIs. It should not import service internals.

---

## Runtime Pipeline Config

Each active CEFS pipeline should be represented as data:

```json
{
  "pipeline_id": "support-bot.hourly",
  "project_id": "support-bot",
  "agent_id": "triage-agent-v2",
  "trace_source_id": "llogr-clickhouse-prod",
  "dataset_sink_id": "dta-annotator-prod",
  "evaluator_profile_id": "support-quality",
  "active_evaluator_version": "support-quality:v3",
  "sampling_policy_id": "daily-cefs-v1",
  "schedule": "0 * * * *",
  "annotation_budget": 200,
  "enabled": true
}
```

Adding a new agent should mean creating or enabling a new pipeline record. The
DAG should discover it on the next scheduled run.

---

## Main DAG

### First Slice: Automated Evaluation

Start with `cefs_auto_evaluate_all_traces`: one generic Airflow DAG that scores
every trace in the Airflow data interval for each active pipeline.

```text
cefs_auto_evaluate_all_traces
  list_active_pipelines
  for each pipeline:
    build_score_request
    create_score_run_with_checkr
    wait_for_score_run_completion
```

This DAG intentionally does **only** automated evaluation. It does not sample
for annotation, create human tasks, or compute meta-evaluation metrics. Those
steps come after the scored-trace pool exists.

The current first implementation lives at
[`airflow/dags/cefs_auto_evaluate_all_traces.py`](../airflow/dags/cefs_auto_evaluate_all_traces.py).
Until the CEFS registry/state API exists, it reads active pipelines from the
Airflow Variable `CEFS_AUTO_EVAL_PIPELINES`:

```json
[
  {
    "pipeline_id": "ai-suite-default.hourly",
    "trace_source_id": "llogr-clickhouse-prod",
    "evaluator_version": "default",
    "enabled": true
  }
]
```

The DAG calls checkr with `pipeline_id`, `trace_source_id`, the Airflow data
interval, `evaluator_version`, `force_rescore`, and an idempotency key. checkr
must load all traces for that window, run the active evaluator, persist score
provenance, and expose score-run status.

### Full CEFS Loop

`cefs_continuous_loop` runs on a cadence and fans out over active pipelines.

```text
cefs_continuous_loop
  list_active_pipelines
  for each pipeline:
    create_cefs_run
    score_new_traces_with_checkr
    run_dataimporter_sampling_job
    submit_or_detect_annotation_run
    wait_for_annotation_completion
    compute_meta_evaluation_metrics
    persist_metrics_and_run_summary
    branch_on_quality_threshold
      ok: mark_run_complete
      below_threshold: open_refinement_candidate
```

The fan-out should use Airflow dynamic task mapping or mapped task groups. The
mapping unit is a pipeline config plus a concrete time window.

---

## DAG Run Config

Manual runs and backfills should accept the same shape:

```json
{
  "pipeline_id": "support-bot.hourly",
  "time_window": {
    "start": "2026-06-01T00:00:00Z",
    "end": "2026-06-01T01:00:00Z"
  },
  "force_rescore": false,
  "force_resample": false,
  "annotation_budget_override": null
}
```

For scheduled runs, Airflow derives the time window from the DAG interval. For
manual runs, the operator supplies it.

---

## Required Service APIs

These APIs do not all exist today, but they are the right orchestration surface.

### Registry / State

```text
GET  /cefs/pipelines?enabled=true
POST /cefs/runs
GET  /cefs/runs/{run_id}
POST /cefs/runs/{run_id}/events
POST /cefs/runs/{run_id}/complete
POST /cefs/runs/{run_id}/fail
```

### checkr

```text
POST /cefs/score-runs
GET  /cefs/score-runs/{score_run_id}
```

Input includes `pipeline_id`, `trace_source_id`, `time_window`,
`evaluator_version`, and idempotency key. Output includes scored trace location,
counts, and score provenance.

Example request from Airflow:

```json
{
  "pipeline_id": "ai-suite-default.hourly",
  "trace_source_id": "llogr-clickhouse-prod",
  "time_window": {
    "start": "2026-06-01T00:00:00Z",
    "end": "2026-06-01T01:00:00Z"
  },
  "evaluator_version": "default",
  "force_rescore": false,
  "idempotency_key": "ai-suite-default.hourly|score_all_traces|2026-06-01T00:00:00Z|2026-06-01T01:00:00Z|default"
}
```

Minimum response:

```json
{
  "score_run_id": "score-run-123",
  "status": "queued"
}
```

Status polling should eventually return `completed` with at least:

```json
{
  "score_run_id": "score-run-123",
  "status": "completed",
  "input_trace_count": 1200,
  "scored_trace_count": 1200,
  "scored_trace_location": "s3://...",
  "evaluator_version": "default"
}
```

### dataimporter

```text
POST /api/public/jobs/run
GET  /api/public/jobs/{job_id}
```

The body should be a validated JobConfig or a reference to a stored JobConfig.
This is the highest-priority gap because CEFS cannot run unattended until
sampling/export can be triggered as a job.

### meta-evaluator

```text
POST /api/v0/runs
GET  /api/v0/runs/{run_id}
GET  /api/v0/summary
```

This exists locally, but state is currently in-memory. Production orchestration
needs durable run identity and idempotent "already processed" detection.

---

## Idempotency

Every task-triggering call must carry an idempotency key derived from:

```text
pipeline_id + step_name + time_window_start + time_window_end + evaluator_version
```

If Airflow retries a task, services should return the existing run/job instead
of creating duplicate score runs, duplicate datasets, or duplicate annotation
tasks.

---

## Suggested DAGs

Use several generic DAGs by workflow shape, not by agent:

| DAG | Purpose |
|---|---|
| `cefs_continuous_loop` | Regular scoring, sampling, annotation, meta-evaluation |
| `cefs_backfill_loop` | Rerun historical traces for a new evaluator version |
| `cefs_experiment_loop` | Compare evaluator version A vs B on the same annotated dataset |
| `cefs_refinement_loop` | Optional candidate prompt/config generation and review |

Each DAG should accept a pipeline ID and runtime config. None should be copied
per project or per agent.

---

## First Implementation Slice

1. Create `cefs_auto_evaluate_all_traces` DAG for one configured pipeline.
2. Implement checkr `POST /cefs/score-runs` and `GET /cefs/score-runs/{score_run_id}`.
3. Add CEFS pipeline records and run records to the registry/state API.
4. Replace the Airflow Variable pipeline config with `list_active_pipelines`.
5. Implement `dataimporter POST /api/public/jobs/run` for annotation sampling/export.
6. Add idempotency keys to dataimporter jobs and meta-evaluator runs.
7. Create `cefs_continuous_loop` by extending the automated evaluation DAG with sampling, annotation, and meta-evaluation.
8. Add a backfill DAG once evaluator versioning exists.
