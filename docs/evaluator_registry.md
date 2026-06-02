# Evaluator Registry and CEFS State

CEFS needs a durable state layer. Airflow can orchestrate the loop, but it should
not be the source of truth for evaluator versions, active pipelines, score
provenance, annotation runs, experiments, or promotion decisions.

This document lists the registry/state gaps required to harden the full CEFS
loop.

---

## Scope

The registry should answer:

- Which CEFS pipelines are active?
- Which evaluator version is active for each pipeline?
- Which traces were scored by which evaluator version?
- Which sampled dataset was sent to annotation?
- Which human labels came back for which scored traces?
- Which meta-evaluation metrics were computed?
- Which experiment compared two evaluator versions?
- Which version was promoted, by whom, and why?

This can start as tables and routes inside `aihub`, or as a small separate
service. The important part is the contract, not the packaging.

---

## Core Entities

### CEFS Pipeline

Represents one production loop for a project/agent/source combination.

Required fields:

```text
pipeline_id
project_id
agent_id
trace_source_id
dataset_sink_id
evaluator_profile_id
sampling_policy_id
schedule
annotation_budget
enabled
created_at
updated_at
```

Notes:

- `trace_source_id` and `dataset_sink_id` should refer to databridge connections.
- Multiple agents can share an evaluator profile.
- New agents should be added by creating a pipeline record, not by creating a new Airflow DAG.

### Evaluator Profile

Logical evaluator family, for example `support-quality` or `sre-resolution`.

Required fields:

```text
profile_id
name
description
owner
default_gate_set
created_at
updated_at
```

### Evaluator Version

Immutable evaluator artifact.

Required fields:

```text
version_id
profile_id
version
status                  # draft | candidate | active | retired | rejected
gate_configs            # prompt/model/threshold/weight per gate
model
model_provider
prompt_hash
config_hash
thresholds
created_by
created_at
activated_at
retired_at
parent_version_id
change_reason
```

Changing a prompt, threshold, model, rubric, or gate weight creates a new
version. Existing versions are never edited in place after activation.

### Active Evaluator Assignment

Maps pipelines to active evaluator versions.

Required fields:

```text
pipeline_id
profile_id
active_version_id
activated_at
activated_by
promotion_decision_id
```

This lets different projects roll forward or roll back independently.

---

## Run State

### CEFS Run

Top-level run for a pipeline and time window.

Required fields:

```text
cefs_run_id
pipeline_id
time_window_start
time_window_end
state                   # queued | scoring | sampling | annotating | evaluating | complete | failed
airflow_dag_id
airflow_run_id
idempotency_key
created_at
started_at
completed_at
failure_reason
```

### Score Run

One invocation of checkr over a trace window.

Required fields:

```text
score_run_id
cefs_run_id
pipeline_id
evaluator_version_id
trace_source_id
time_window_start
time_window_end
input_trace_count
scored_trace_count
score_output_uri
state
created_at
completed_at
```

### Score Record Provenance

Every score must carry enough metadata to be auditable later.

Required fields:

```text
trace_id
span_id
score_run_id
pipeline_id
project_id
agent_id
gate_name
evaluator_profile_id
evaluator_version_id
model
prompt_hash
config_hash
threshold
score
verdict                 # PASS | FAIL
confidence
rationale_uri
created_at
```

This is the contract that lets meta-evaluator group quality by evaluator
version, gate, agent, model, or prompt/config hash.

### Sampling Run

Captures the dataimporter selection/export step.

Required fields:

```text
sampling_run_id
cefs_run_id
score_run_id
job_config_id
dataimporter_job_id
sampling_policy_id
selected_trace_count
dataset_id
dataset_uri
state
created_at
completed_at
```

### Annotation Run

Links the sampled dataset to annotator tasks and returned labels.

Required fields:

```text
annotation_run_id
cefs_run_id
sampling_run_id
annotation_provider      # annotator-mock | dta-annotator
annotation_project_id
annotation_task_id
overlap
state
created_at
completed_at
```

### Annotation Label

Durable human label or consensus label.

Required fields:

```text
annotation_label_id
annotation_run_id
trace_id
score_record_id
annotator_id
label                   # AGREE | DISAGREE | PARTIAL
confidence
note
created_at
```

Consensus labels can either be materialized into a separate table or stored as
derived meta-evaluator output.

### Meta-Evaluation Run

Computed evaluator-quality metrics.

Required fields:

```text
meta_eval_run_id
cefs_run_id
annotation_run_id
evaluator_version_id
metric_scope             # profile | gate | agent | project
agreement_rate
accuracy
precision
recall
f1
cohens_kappa
sample_size
partial_count
created_at
```

---

## Experiment and Promotion State

### Experiment Run

Compares evaluator versions on the same annotated dataset.

Required fields:

```text
experiment_run_id
profile_id
pipeline_id
baseline_version_id
candidate_version_id
annotation_dataset_id
state
baseline_metrics
candidate_metrics
delta_metrics
recommendation          # promote | reject | needs_review
created_at
completed_at
```

### Promotion Decision

Records activation or rejection.

Required fields:

```text
promotion_decision_id
profile_id
pipeline_id
from_version_id
to_version_id
experiment_run_id
decision                # promoted | rejected | rolled_back
reason
decided_by
decided_at
```

Promotion should be explicit. Even if an automated policy recommends promotion,
the decision record should preserve what changed and why.

---

## API Surface

Minimal first-pass API:

```text
GET  /cefs/pipelines?enabled=true
POST /cefs/pipelines
GET  /cefs/pipelines/{pipeline_id}
PATCH /cefs/pipelines/{pipeline_id}

GET  /cefs/evaluator-profiles
POST /cefs/evaluator-profiles
GET  /cefs/evaluator-profiles/{profile_id}/versions
POST /cefs/evaluator-profiles/{profile_id}/versions
GET  /cefs/evaluator-versions/{version_id}

GET  /cefs/pipelines/{pipeline_id}/active-evaluator
POST /cefs/pipelines/{pipeline_id}/active-evaluator

POST /cefs/runs
GET  /cefs/runs/{cefs_run_id}
POST /cefs/runs/{cefs_run_id}/events
POST /cefs/runs/{cefs_run_id}/complete
POST /cefs/runs/{cefs_run_id}/fail

POST /cefs/score-runs
POST /cefs/sampling-runs
POST /cefs/annotation-runs
POST /cefs/meta-evaluation-runs

POST /cefs/experiment-runs
POST /cefs/promotion-decisions
```

The API can be implemented incrementally. The first priority is pipeline
discovery, evaluator-version lookup, CEFS run state, and score provenance.

---

## Current Gaps

| Gap | Impact | Priority |
|---|---|---|
| No active pipeline registry | Airflow cannot discover new projects/agents dynamically | High |
| No evaluator profile/version model | checkr cannot load or report evaluator versions consistently | High |
| No active evaluator assignment | No safe rollback or per-pipeline promotion | High |
| No score provenance contract | Meta-evaluation metrics cannot be tied to a specific evaluator version | High |
| No durable CEFS run table | Airflow retries and service restarts can duplicate work | High |
| No durable meta-evaluator run storage | Current in-memory run state is not production-safe | High |
| No annotation label store | Human labels are not first-class reusable training/evaluation assets | Medium |
| No experiment run model | Candidate evaluators cannot be compared repeatably | Medium |
| No promotion decision record | Evaluator changes are not auditable | Medium |
| No checkr reload/promotion hook | Active versions cannot be applied without manual process | Medium |

---

## Implementation Order

1. Add CEFS pipeline and run tables/API.
2. Add evaluator profile/version tables/API.
3. Add active evaluator assignment per pipeline.
4. Add score provenance fields to checkr outputs.
5. Persist meta-evaluator runs and labels.
6. Add experiment run and promotion decision tables.
7. Add checkr reload or restart hook for active-version changes.

