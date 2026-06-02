# Continuous Evaluator Refinement System (CEFS)

> **Core thesis:** LLM evaluators (judges) are models too — they degrade, bias, and drift. CEFS is the feedback loop that continuously validates and improves the evaluators themselves, not just the LLM outputs they judge.

---

## The Problem

You deploy an LLM feature. You add an LLM-as-judge to score response quality. Traffic flows. Dashboards look fine. But:

- The judge was calibrated on 200 hand-picked examples. Production distribution has shifted.
- The judge says 87% of responses are "good". Nobody has checked if the judge is right.
- A new model was swapped in. The judge's prompts still reference the old model's quirks.
- Evaluator quality is invisible — there are no metrics on the evaluator itself.

CEFS closes this blind spot by turning evaluator quality into a first-class, continuously measured, and continuously improved property of the system.

---

## System Goal

Build a closed loop where:

1. **Production LLM calls are captured** as structured traces.
2. **Evaluators automatically score** those traces (LLM-as-judge).
3. **A sample of scored traces surfaces to humans** for ground-truth annotation.
4. **Evaluator quality is computed** as the agreement between the judge and humans.
5. **Discrepancies drive refinement** — prompts, models, thresholds are updated.
6. **Refined evaluators re-run on historical data** to backfill quality signals.
7. Loop repeats on a cadence.

---

## The Continuous Loop

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│   ① PRODUCTION TRAFFIC                                                          │
│   LLM agents & apps call the LLM proxy (yallmp)                                │
│   Every call is traced → llogr (S3 + ClickHouse) + langfuse                    │
│                                                                                 │
│   ② AUTOMATED EVALUATION  (async, decoupled from request path)                  │
│   checkr consumes ALL traces from llogr as a batch job — not as middleware.     │
│   Evaluation never blocks the user's request; it runs after the fact.           │
│   Scores + pass/fail results are posted back to langfuse as trace scores        │
│   and stored in aihub leaderboard.                                              │
│   100% coverage is the default; a sample-rate knob exists as a cost governor   │
│   (relevant only for expensive cloud judge models).                             │
│                                                                                 │
│   ③ ANNOTATION SAMPLING  (from already-evaluated traces)                        │
│   Sampling happens AFTER evaluation, not instead of it.                         │
│   dataimporter selects a policy-driven subset of scored traces for human review:│
│   – borderline cases (scores near gate thresholds — judge least confident)      │
│   – disagreement cases (evaluator versions disagree on the same trace)          │
│   – stratified random baseline (uniform across score distribution + time)       │
│   Exported as a labeled dataset to the dataset service → annotation queue       │
│                                                                                 │
│   ④ HUMAN ANNOTATION                                                            │
│   annotator-mock exists today as the local annotation platform stand-in.         │
│   It creates projects/tasks, simulates overlap=3 reviewers, and emits labels:   │
│   Agree / Disagree / Partial + optional notes.                                  │
│   Production hardening means swapping the mock for the real DTA annotator API.  │
│                                                                                 │
│   ⑤ EVALUATOR QUALITY MEASUREMENT                                               │
│   meta-evaluator exists today and computes per-run metrics:                     │
│   – Accuracy vs human labels                                                    │
│   – Precision / Recall per gate                                                 │
│   – F1 and Cohen's κ                                                            │
│   Results exposed via /metrics and /api/v0/summary                              │
│                                                                                 │
│   ⑥ EVALUATOR REFINEMENT  ← [STILL MISSING]                                     │
│   Where accuracy < threshold: trigger refinement cycle                          │
│   – Propose updated prompts / model / gate config                               │
│   – Register as new evaluator version in evaluator registry                    │
│   – Run experiment: new version vs current on the annotated dataset             │
│   – Promote if better; reject if worse                                          │
│                                                                                 │
│   ⑦ BACK TO ①  (evaluators update; next production traces use new version)      │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Services: Existing vs Missing

Status note: this repo already contains `meta-evaluator`, `annotator-mock`, and
`databridge`. The remaining CEFS work is less about inventing more services and
more about adding the orchestration and persistence needed to make the loop run
on a cadence.

### Tier 1 — Traffic & Trace Capture

| Service | Role in CEFS | Status |
|---------|-------------|--------|
| **yallmp** | LLM proxy — intercepts all production traffic; adds cost, token, and latency telemetry; routes traces to llogr | ✅ Exists |
| **llogr** | Langfuse-compatible trace store; persists to S3 (raw) + ClickHouse (indexed); fan-out to langfuse UI; export API for dataset pipelines | ✅ Exists |
| **langfuse** | Trace UI, score visualization, dataset management; receives traces from llogr fan-out | ✅ Exists (bundled) |
| **clickstream** | User-action event stream (clicks, feedback buttons); feeds human signals about response quality | ✅ Exists |

**What flows here:** Every LLM call → trace event → S3 + ClickHouse + langfuse.

---

### Tier 2 — Automated Evaluation

| Service | Role in CEFS | Status |
|---------|-------------|--------|
| **checkr** | Multi-gate dataset/trace validator; LLM-as-judge gates (G-Eval, GABRIEL); posts scores back to langfuse; validates datasets before they enter the training/refinement pipeline | ✅ Exists |
| **typical_agent** | Demo SRE agent; illustrates the self-evaluation pattern (evaluate_quality step posts a score to langfuse after each run) | ✅ Exists (demo) |

**What flows here:** All traces → checkr (async batch) → pass/fail + scores per gate → langfuse scores + aihub leaderboard.

**Evaluation scope:** checkr runs on **all traces by default**, asynchronously — fully decoupled from the request path so there is zero latency impact on users. A configurable sample rate acts as a cost governor for expensive cloud judge models; with a local model via yallmp it is always 100%.

**Sampling for annotation is a separate concern:** the smart sampler in dataimporter operates on the pool of *already-scored* traces to pick the highest-signal subset for human review. This is not the same as reducing evaluation coverage.

**Gap:** checkr evaluators are statically configured. There is no versioning, no A/B testing between evaluator configs, no registry of active evaluator versions. The async batch trigger also does not exist yet — currently checkr is invoked on-demand, not as a continuous consumer of new traces.

---

### Tier 3 — Dataset Curation & Import

| Service | Role in CEFS | Status |
|---------|-------------|--------|
| **dataimporter** | Browses S3/ClickHouse/langfuse trace stores; selects and imports traces into external dataset services; supports stratified sampling, time-range filtering, search | ✅ Exists |
| **databridge** | Connection management service for S3, ClickHouse, Trino, Langfuse, and dataset sinks; centralizes credentials and preview/schema operations for pipeline sources and sinks | ✅ Exists |
| **dataset-mock** | Local mock of the target dataset service; used for integration testing the import flow | ✅ Exists (test) |

**What flows here:** Scored traces → dataimporter selects a sample → imports into dataset service as a labeled dataset.

**Current state:** dataimporter already has a v1 sampling engine (`random`, high-cost, latency spike, long trace, failure, user dissatisfaction, business critical, prompt/version change, low confidence, weird tool sequences) and a JobConfig schema/validator.

**Gap:** The sampler exists, but CEFS still needs a job runner/scheduler API that can execute a saved JobConfig on a cadence. See [drafts/dataimporter-job-config-api-gaps.md](drafts/dataimporter-job-config-api-gaps.md). The v2 sampling signals also remain open: judge disagreement, drift, active learning, retrieval failure, and span slicing.

---

### Tier 4 — Scoring, Leaderboard & Observability

| Service | Role in CEFS | Status |
|---------|-------------|--------|
| **aihub** | FastAPI + PostgreSQL; leaderboard (model/evaluator performance rankings); chat history (per-user history with scores); future durable home for CEFS run summaries, annotations, and evaluator-version metrics | ✅ Exists / 🟡 CEFS tables missing |
| **prometheus + grafana** | Time-series metrics + dashboards for all services; tracks evaluator quality trends over time | ✅ Exists |
| **minio** | S3-compatible object store; holds raw trace JSONL, dataset files, media blobs | ✅ Exists |

---

### Tier 5 — Evaluator Refinement

The annotation and meta-evaluation pieces now exist locally. The missing work is
the promotion/refinement side of the loop and production persistence around the
mocked pieces.

| Service | Role in CEFS | Priority |
|---------|-------------|----------|
| **annotator-mock / DTA annotator** | annotator-mock implements the local API and simulated labels; production loop should point the same orchestration at the real DTA annotator or replace the mock adapter | ✅ Exists locally / 🟡 prod adapter |
| **meta-evaluator** | Orchestrates dataset-mock → annotator-mock → metrics; computes agreement rate, accuracy, precision, recall, F1, Cohen's κ; exposes Prometheus metrics and summaries | ✅ Exists |
| **evaluator registry** | Version-controlled store of evaluator configs (gate prompts, model, thresholds, weights); checkr reads the active version on startup; enables rollback and A/B testing | 🟡 Medium |
| **experiment runner** | Orchestrates evaluator A/B experiments: takes two evaluator versions + an annotated dataset → runs both → computes delta in accuracy → produces promotion recommendation | 🟡 Medium |
| **refinement orchestrator** | Trigger on low evaluator accuracy; propose prompt edits (optionally LLM-assisted); register in evaluator registry; kick off experiment runner; promote winner | 🟠 Later |

---

## End-to-End Data Flow

```
Production LLM apps
        │
        ▼ (OpenAI-compatible API calls, sync)
   ┌─────────┐   traces    ┌───────────┐   store    ┌─────────────────────────┐
   │ yallmp  │────────────►│  llogr    │───────────►│ S3 (minio) + ClickHouse │
   └─────────┘             └─────┬─────┘            └────────────┬────────────┘
                                 │ fan-out                        │
                                 ▼                                │ batch query (async)
                          ┌───────────┐                           │
                          │ langfuse  │             ┌─────────────▼──────┐
                          └───────────┘             │      checkr        │
                                                    │  (evaluates ALL    │
                                                    │   traces, async)   │
                                                    └──┬─────────────────┘
                                                       │ scores (100% of traces)
                                          ┌────────────┴────────────┐
                                          ▼                         ▼
                                   langfuse scores          aihub leaderboard
                                   (quality monitoring)

                                   ── annotation sampling ──────────────────────
                                   (separate step, from scored traces pool)

                                         ┌────────────────────────┐
                                         │     dataimporter       │
                                         │  smart sampler:        │
                                         │  – borderline scores   │
                                         │  – version disagreement│
                                         │  – random baseline     │
                                         └────────────┬───────────┘
                                                      │ import (small high-signal subset)
                                                      ▼
                                             ┌──────────────────┐
                                             │ dataset service  │
                                             │  (external /     │
                                             │   dataset-mock)  │
                                             └────────┬─────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │ annotator-mock  │
                                             │ / DTA annotator │
                                             └────────┬────────┘
                                                      │ ground truth
                                                      ▼
                                             ┌─────────────────┐
                                             │ meta-evaluator  │
                                             │ (accuracy, F1,  │
                                             │  Cohen's κ)     │
                                             └────────┬────────┘
                                                      │ metrics
                                         ┌────────────┴────────────┐
                                         ▼                         ▼
                                  grafana dashboard         aihub leaderboard
                                         │
                             (accuracy < threshold?)
                                         │ yes
                                         ▼
                                ┌────────────────────┐
                                │ evaluator registry │  ← MISSING
                                │ + experiment runner│
                                └────────┬───────────┘
                                         │ new evaluator version
                                         ▼
                                     checkr (reload)
                                         │
                                         └──────────────► back to top
```

---

## Key Design Principles

### 1. Evaluators are versioned artifacts
A gate in checkr is not just code — it's a versioned combination of (prompt, model, threshold, weights). Changing a prompt creates a new version. The evaluator registry is the single source of truth. checkr loads the active version on startup or via a config reload endpoint.

### 2. Ground truth is always human-in-the-loop
The annotator service is the only source of ground truth. LLM-generated labels from the meta-evaluator are diagnostic — they reveal where the evaluator struggles — but they are never substituted for human labels in accuracy computation.

### 3. Evaluation covers all traces; sampling is only for the annotation queue
These are two different operations that must not be conflated:

- **Evaluation (checkr)** runs on **all traces**, asynchronously. Sampling here reduces quality-monitoring fidelity — a 10% sample on a low-traffic app gives too few data points to detect drift. Sampling is a cost knob, not a design default.
- **Annotation sampling (dataimporter)** selects a small, high-signal subset of *already-evaluated* traces for human review. Humans are the bottleneck; you want the most informative traces, not all of them.

Sampling criteria must come from **observable signals in the trace** — metadata, spans, metrics, user behaviour, evaluator outputs, trace topology — not manual intuition. The right model is a **priority scoring pipeline**, not a set of additive percentages:

```
sampling_priority =
    failure_weight + cost_weight + novelty_weight +
    dissatisfaction_weight + risk_weight + judge_disagreement_weight
```

Select top-N traces per day by score. Build a **trace taxonomy** first (trace_type × intent × risk × failure_mode) and apply per-bucket quotas so dominant categories don't consume the entire annotation budget.

→ Full signal catalogue, taxonomy, three-layer design, and example budget allocation: **[sampling.md](sampling.md)**

### 4. Metrics flow through existing infrastructure
No new time-series DB. Evaluator quality metrics (accuracy, drift) are just Prometheus gauges pushed from the meta-evaluator, visualized in grafana. aihub leaderboard adds a ranking layer on top.

### 5. checkr is the evaluation engine — CEFS is the feedback loop around it
checkr runs evaluations. CEFS tells you whether those evaluations are trustworthy, and closes the loop to make them better. These are separate concerns and should stay separate services.

---

## Orchestration and State

Use Airflow as the outer orchestrator for CEFS cadence, retries, backfills, and
manual reruns. Do not create one DAG per agent. Instead, define generic DAGs
that read active CEFS pipeline records and dynamically map tasks across
project/agent/datasource configurations.

Airflow should call service APIs; it should not own CEFS domain logic. The
durable source of truth should be a CEFS registry/state API, implemented either
inside `aihub` or as a small dedicated service.

Recommended split:

| Layer | Responsibility |
|---|---|
| **Airflow** | schedule, dependency graph, retries, backfills, manual reruns |
| **CEFS registry/state** | active pipelines, evaluator versions, score provenance, run state, promotion decisions |
| **checkr** | evaluate traces with a specific evaluator version |
| **dataimporter** | sample/export already-scored traces through JobConfig jobs |
| **meta-evaluator** | annotation orchestration and evaluator-quality metrics |

Detailed design:

- [orchestration.md](orchestration.md) — Airflow DAG shape, dynamic pipeline mapping, service API boundaries.
- [evaluator_registry.md](evaluator_registry.md) — CEFS state model, evaluator versioning gaps, score provenance, promotion records.

---

## What to Build First

### Phase 0 — Run the current local loop

**Goal:** prove the existing services can move one scored dataset through annotation and evaluator-quality measurement.

1. Produce a JSONL dataset whose records include at least `trace_id`, `verdict` (`PASS`/`FAIL`), and `score` (`0..1`).
2. Import that dataset into `dataset-mock` through dataimporter.
3. Let `meta-evaluator` auto-poll `dataset-mock`, create an annotation task in `annotator-mock`, wait for completion, and compute metrics.
4. Read `meta-evaluator /api/v0/summary` and Prometheus `/metrics`.

This gives a closed local measurement loop, but it is not yet a continuous production loop.

### Phase 1 — Make it continuous

**Goal:** remove the manual handoffs between trace capture, checkr, dataimporter, and meta-evaluator.

1. **checkr trace consumer** — scheduled/batch worker that reads new traces from llogr/ClickHouse, runs the selected gates, and writes scored trace records back to Langfuse and/or a dataset sink.
2. **dataimporter jobs/run API** — endpoint that accepts a validated JobConfig and executes it. This is the bridge from "schema exists" to "CEFS can run unattended".
3. **CEFS registry/state API** — persist active pipelines, evaluator versions, run state, score provenance, and promotion decisions. This is what lets Airflow run the same DAG dynamically for many agents and datasources.
4. **dta-annotator adapter** — keep the meta-evaluator orchestration shape, but point it at the real `dta-annotator` instead of `annotator-mock`.
5. **scheduled JobConfig storage** — persist CEFS sampling jobs and run history. A daily job can then select the annotation budget from already-scored traces.
6. **Airflow `cefs_continuous_loop` DAG** — one generic DAG that discovers enabled CEFS pipelines and dynamically maps the scoring/sampling/annotation/meta-evaluation task group.
7. **persist annotation/evaluation runs** — `meta-evaluator` currently keeps run state in memory; production needs a DB table or aihub resource for runs, labels, metrics, and source dataset IDs.

At the end of Phase 1 the full loop is operational but still manually refined:
new traces are scored, sampled, sent to annotation, measured by meta-evaluator,
and visible in metrics without a human clicking through the import UI.

### Phase 2 — Evaluator versioning

8. **Evaluator registry completion** — active evaluator assignment per pipeline, immutable evaluator versions, and rollback support.
9. **Score provenance** — every checkr score must include evaluator version, gate name, model, prompt/config hash, threshold, trace ID, and timestamp. Without this, meta-evaluator cannot tell which judge version was right or wrong.
10. **Airflow `cefs_backfill_loop` DAG** — when a new evaluator version is created, rerun it on the annotated historical dataset so the comparison is apples-to-apples.

### Phase 3 — Promotion and refinement

11. **Airflow `cefs_experiment_loop` DAG** — takes two evaluator versions plus an annotated dataset, runs both, computes delta accuracy/calibration, and stores a promotion recommendation.
12. **Promotion endpoint** — marks a registry version active and tells checkr to reload or restarts checkr safely.
13. **Refinement orchestrator** — watches meta-evaluator metrics; when accuracy drops below threshold, opens a refinement task, optionally proposes prompt/config edits, and routes the candidate through the experiment runner.

---

## Gaps Summary

| Gap | Impact | Fills Phase |
|-----|--------|-------------|
| No async batch evaluation trigger | checkr is invoked on-demand today; needs to continuously consume new traces from llogr | 1 |
| No dataimporter jobs/run API | JobConfig validates, but cannot yet be submitted as the unit of scheduled CEFS work | 1 |
| No Airflow orchestration DAG | Full loop cannot run on a cadence with retries, backfills, and dynamic project/agent fan-out | 1 |
| No active CEFS pipeline registry | New agents/datasources cannot be discovered dynamically by one generic DAG | 1 |
| No scheduled JobConfig storage/history | Cannot run annotation sampling daily/weekly without manual UI work | 1 |
| meta-evaluator state is in-memory | Restart loses seen datasets and run history; duplicate processing is possible | 1 |
| annotator-mock is not the production DTA annotator | Local loop works, but production labels need the real annotator adapter/API | 1 |
| No score provenance contract | Metrics cannot be reliably grouped by evaluator version/gate/model/prompt hash | 2 |
| No evaluator versioning registry | Can't track which prompt/model change improved accuracy | 2 |
| No backfill runner | New evaluator versions cannot be compared against old versions on the same traces | 2 |
| No experiment runner | Can't safely A/B test evaluator candidates | 3 |
| No promotion/reload path | Even a better evaluator version cannot automatically become active in checkr | 3 |
| No refinement orchestrator | Loop is still manual; humans must notice drift and act | 3 |
