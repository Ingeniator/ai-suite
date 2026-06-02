# Trace Sampling Strategy

Sampling in CEFS decides which traces from the 100%-covered automated evaluation pool
are forwarded to the human annotation queue. It is **not** about reducing evaluation
coverage — checkr runs on all traces asynchronously. Sampling only controls the
annotation budget.

---

## Mental Model

| Concept | Definition |
|---|---|
| **Evaluation coverage** | How many traces checkr scores — default 100%, cost-knob only |
| **Annotation budget** | How many traces humans can review per day/week — the real constraint |
| **Sampling strategy** | How the annotation budget is allocated across trace categories |
| **Sampling percentage** | A capture rule applied *within* a category, not globally added |

> **Key insight:** `20% random + 10% high-cost ≠ 30% total`.
> The same trace can match both criteria.
> Total = `|union(random_sample, high_cost_sample, ...)|` — typically less than the sum.
> If the union reaches 100%, disable all sampling and take everything.

---

## Observable Signals

Sampling criteria must come from **observable signals in the trace**, not manual intuition.
A mature trace contains:

```
trace_id          session_id        user_id           model
prompt_version    tool_calls        latency           cost
token_usage       retrieval_chunks  judge_scores      feedback
errors            span_tree         retry_count       fallback_used
```

Sampling = querying this dataset intelligently.

---

## Trace Taxonomy

Before choosing sampling percentages, classify traces into a taxonomy.
Sampling applied per bucket prevents dominant categories from consuming the entire budget.

```yaml
trace_type:
  - simple_answer          # single-turn, no tools
  - rag_answer             # retrieval-augmented
  - tool_execution         # one or more tool calls
  - multi_step_agent       # multi-turn with planning
  - recovery_flow          # failed + retried + succeeded
  - human_escalation       # transferred to human

intent:
  - support_question
  - data_analysis
  - code_generation
  - workflow_automation

risk:
  - low
  - medium
  - high

failure_mode:             # populated only when applicable
  - retrieval_miss
  - bad_tool_choice
  - loop
  - hallucination
  - schema_error
  - timeout
```

---

## Sampling Signal Catalogue

### 1. Random Baseline
**Goal:** General health check; ensures every category has representation.

```sql
-- Stratified: 1% per model, per workflow, per tenant
WHERE random() < 0.01
GROUP BY model, workflow, tenant
```

Without stratification, high-traffic categories dominate and low-traffic ones go unobserved.

---

### 2. Failure-Based
**Goal:** Catch regressions before they compound.

```
trace.status == "error"
tool.error_count > 0
response.valid_json == false
trace.latency > timeout_threshold
retry_count > N
new_failure_signature == true          # first occurrence of this error pattern
failure_rate(window=1h) > baseline     # rate spike, not just one-off
```

---

### 3. User Dissatisfaction
**Goal:** Capture explicit and implicit negative signals.

```
thumbs_down == true
user_clicked_regenerate == true
same_user_same_question_within_5m      # implicit: answer didn't satisfy
conversation.transferred_to_human == true
conversation_ended_after_response == true  # abandonment after long/expensive trace
```

---

### 4. High-Cost
**Goal:** Find traces where token/money spend was disproportionate.

```
trace.total_tokens > p95
trace.cost_usd > threshold
tool_call_count > p95
context_window_utilization > 80%
```

---

### 5. Long / Deep Traces
**Goal:** Detect agent degradation — loops, excessive branching, stuck behavior.

```
trace.span_count > threshold
trace.max_depth > threshold
repeated_tool_pattern == true          # search → summarize → search → summarize
same_prompt_embedding_similarity > 0.95  # stuck, re-asking the same thing
```

---

### 6. Novelty / Outlier
**Goal:** Discover behavior the system has never seen before.

```
distance_to_nearest_cluster > threshold
tool_sequence not in historical_patterns
intent_frequency < rarity_threshold
retrieval_source unseen_recently
```

---

### 7. Low Confidence
**Goal:** Surface cases where the system was uncertain.

```
judge.score_variance > threshold           # LLM judge fluctuates on re-runs
agent.confidence < threshold               # self-reported (use cautiously)
retrieval_similarity < threshold           # weak retrieval
retrieved_docs_conflict == true            # contradictory evidence retrieved
```

---

### 8. Version Change
**Goal:** Catch regressions introduced by any system change.

```
prompt_version != baseline_version
model_version changed
retrieval_index_version changed
tool_hash changed
```

Best practice: replay the **same inputs** through old and new system, sample the diffs.

---

### 9. Business-Critical
**Goal:** Guarantee coverage of high-stakes workflows regardless of other signals.

```
workflow in critical_workflows
tenant.tier == "enterprise"
trace.contains_action("payment")
trace.requires_approval == true
```

---

### 10. Retrieval Failure
**Goal:** Catch RAG-specific failure modes.

```
avg_chunk_similarity < threshold
response_claims_without_sources > threshold
retrieved_chunks_not_referenced == true    # retrieved but ignored
retrieved_chunk_count > optimal_range      # context dilution
```

---

### 11. Tool-Sequence Anomaly
**Goal:** Detect invalid or inefficient tool usage patterns.

```
tool_sequence violates policy
same_tool_called_repeatedly
required_tool_not_called
parallel_tool_branches > threshold
```

---

### 12. Recovery Behavior
**Goal:** Study how the system handles failures — excellent training material.

```
tool_failed AND final_success == true      # recovered — learn from this
fallback_model_used == true
retry_count > 0 AND final_success == true
degraded_mode == true
```

---

### 13. Latency Spike
**Goal:** Identify slow paths before they become user-facing issues.

```
trace.latency > p99
slowest_span > threshold
tool_wait_time / total_time > threshold
```

---

### 14. Judge Disagreement
**Goal:** Find where the evaluator is least reliable — highest value for annotation.

```
abs(human_score - llm_score) > threshold       # retrospective, after annotations exist
judge_variance > threshold                      # multi-judge setup
same_score_but_conflicting_reasoning            # judge is inconsistent
```

---

### 15. Active Learning (Composite)
**Goal:** Maximize information gain per annotation dollar.

```
priority =
    uncertainty_score   ×  uncertainty_weight   +
    novelty_score       ×  novelty_weight        +
    business_impact     ×  impact_weight
```

Select top-N traces by `priority` per time window.

---

### 16. Drift
**Goal:** Detect distribution shift before it shows up in quality metrics.

```
current_intent_distribution != baseline_distribution
current_embeddings shifted beyond threshold
new_languages_detected
seasonal_behavior_detected
```

---

## Priority Scoring Pipeline

> Do not build one sampling strategy. Build a **scoring pipeline**.

Instead of applying rules independently and unioning the results naively, assign each
trace a composite priority score and select the top N per budget window:

```
sampling_priority =
    failure_weight          * is_failure           +
    cost_weight             * is_high_cost         +
    novelty_weight          * novelty_score        +
    dissatisfaction_weight  * has_negative_signal  +
    risk_weight             * risk_level           +
    business_impact_weight  * is_critical_workflow +
    judge_disagreement_w    * judge_variance       +
    recovery_weight         * is_recovery_flow
```

Example weights for an agentic SRE system:

| Signal | Score |
|---|---|
| High-risk workflow (payment, auth) | +50 |
| New / unseen trace category | +30 |
| Tool error | +25 |
| P95 token cost | +15 |
| Recovery flow (failed → succeeded) | +15 |
| Retrieval failure | +15 |
| Judge score variance > 0.3 | +20 |
| Random baseline (always eligible) | +5 |

Select top 1,000 (or N) traces per day sorted by score descending.

---

## Three-Layer Design

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1 — Always Include (no budget limit)                 │
│  · Critical failures (auth down, payment error)             │
│  · Policy / compliance events                               │
│  · Security-flagged traces                                  │
├─────────────────────────────────────────────────────────────┤
│  Layer 2 — Stratified Sampling (budget: ~60% of daily N)    │
│  · One sample per taxonomy bucket (trace_type × risk)       │
│  · Prevents dominant categories from eating the budget      │
│  · Baseline health coverage across all system behaviors     │
├─────────────────────────────────────────────────────────────┤
│  Layer 3 — Exploration (budget: ~40% of daily N)            │
│  · Novelty / outliers                                       │
│  · New tool sequences                                       │
│  · Drift-detected categories                                │
│  · Active learning top-N by composite priority score        │
└─────────────────────────────────────────────────────────────┘
```

---

## Example Annotation Budget Allocation (1,000 traces/day)

| Category | Count | Signal |
|---|---|---|
| Random stratified baseline | 300 | Health across all categories |
| Failures / suspicious | 250 | Regression detection |
| High-risk business flows | 150 | Business-critical coverage |
| High-cost / long traces | 100 | Agent degradation |
| Novelty / rare taxonomy | 100 | Blind-spot discovery |
| Recovery flows | 100 | Model improvement seeds |
| **Total** | **1,000** | |

This is **not** "X% of all traces". It is an **evaluation budget allocation** across
trace categories defined by the taxonomy.

---

## Practical Starting Configuration

For a new agentic system without annotation history yet:

| Signal | Initial Filter |
|---|---|
| Errors | any tool error |
| Cost | token > p95 |
| Length | span count > p95 |
| Recovery | retry_count > 0 |
| Novelty | unseen tool chain |
| Feedback | thumbs down |
| Retrieval | similarity < threshold |
| Regression | changed prompt/model version |

Start with these eight. Add taxonomy classification once enough traces are collected
to identify meaningful clusters. Migrate to full priority scoring in Phase 2.

---

## Relationship to CEFS Phases

| Phase | Sampling maturity |
|---|---|
| Phase 1 (MVP) | Failure + cost + random stratified; manual budget N |
| Phase 2 | Taxonomy classification; per-bucket quotas; judge-disagreement signal |
| Phase 3 | Full priority scoring pipeline; active learning; drift-triggered resampling |
