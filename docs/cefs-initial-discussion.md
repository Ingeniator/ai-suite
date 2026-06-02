# CEFS — Initial Design Discussion

> Source: ChatGPT conversation (https://chatgpt.com/share/6a13fdfb-e514-83eb-88fa-00cb15734f23)
> Captured: 2026-05-25
> Purpose: seed material for the CEFS architecture

This document preserves the original discussion that seeded the CEFS design.
Conclusions from it are incorporated into [cefs.md](cefs.md) and [sampling.md](sampling.md).

---

## Core Question

*How can I observe the quality of a custom AI agent? I need traces (e.g. Langfuse) and
metrics — but how do I score success? LLM-as-judge, annotation platform? On what part
of traces? How do I select the correct subset?*

---

## Key Insight: Traces ≠ Evaluation

Tools like Langfuse, LangSmith, Helicone, or OpenTelemetry are execution tracing and
metadata collection. They answer **what happened** — not **whether it was good**.

| Tracing answers | Evaluation answers |
|---|---|
| What happened? | Was the answer good? |
| Which prompt/model/tool/version? | Was the tool choice correct? |
| Which step failed? | Was reasoning efficient? |
| Where did latency explode? | Did the user succeed? |
| Which retrieval docs were used? | Was the response grounded? |

Evaluation requires a separate pipeline on top of traces.

---

## Observability Layers for AI Agents

Observability for agents evolves into 4 layers:

1. **Infrastructure observability** — latency, cost, errors
2. **Behavior tracing** — what the agent actually did
3. **Quality evaluation** — whether the result was good
4. **Product outcome metrics** — whether users succeeded

Most teams stop at layers 1–2. The real value starts at layer 3.

---

## Evaluation Units: Score Nodes, Not Only Final Output

A trace is a DAG of decisions. Quality exists at multiple granularities:

| Layer | Example question |
|---|---|
| Final response | Was the answer correct/helpful? |
| Tool selection | Did the agent call the right tool? |
| Retrieval quality | Were the retrieved docs relevant? |
| Planning quality | Was the plan coherent? |
| Reasoning efficiency | Too many loops? |
| Safety/compliance | Hallucinations? Policy violations? |
| UX quality | Too verbose? Annoying? |
| Business outcome | Did the user complete the task? |

---

## What Should Be Automatically Scored?

### A. Deterministic metrics (cheap, reliable)

```
latency                    token count
tool errors                retry count
hallucination regex        JSON schema validity
citation existence         API success rate
loop count                 retrieval overlap
task completion signal
```

Production-grade examples:
```
tool_success_rate
avg_tool_calls
retrieval_precision
response_schema_valid
cost_per_success
```

### B. LLM-as-judge

Typical pattern:
```
INPUT + EXPECTED BEHAVIOR + TRACE + FINAL RESPONSE
  → Judge LLM
  → Score + rationale
```

Can evaluate: correctness, groundedness, tool appropriateness, completeness,
reasoning quality, instruction adherence, safety, tone, efficiency.

Relevant frameworks: Ragas, DeepEval, Promptfoo, OpenAI Evals, Braintrust.

### C. Human annotation

Still essential because:
- Judges drift
- Business nuance matters
- UX quality is subtle
- Agentic behavior is weird

Humans especially needed for: edge cases, failures, ambiguous tasks, high-value
flows, new agent capabilities.

---

## LLM-as-Judge Pitfalls

LLM judges are:
- Biased toward verbose answers
- Biased toward their own model family
- Unstable on re-runs
- Vulnerable to prompt leakage
- Weak on factuality without references

Mitigations:
- Use rubric-based judging with constrained output format
- Use pairwise comparisons (A vs B) rather than absolute scores
- Calibrate periodically against human labels
- Track judge score variance as a signal of instability

**The right direction:**
```
Human labels → calibrate judge → judge scales up
```
Not: LLM judge replaces humans.

---

## What to Evaluate in Agent Traces Specifically

| Aspect | What to check |
|---|---|
| **Planning** | Was the task decomposition reasonable? |
| **Tool routing** | Did it call the right tools? |
| **Retrieval** | Were retrieved docs relevant? |
| **Context management** | Did it lose important context across turns? |
| **Loop control** | Did it spiral into repeated calls? |
| **Recovery behavior** | Did it recover gracefully from failures? |
| **Final synthesis** | Was the answer coherent and grounded? |

---

## Agent Scorecards (Multi-Dimensional)

Advanced teams rarely use one score. Instead they build scorecards:

```
Final correctness:    0–5
Groundedness:         0–5
Tool efficiency:      0–5
Task completion:      yes/no
Safety:               pass/fail
Latency:              ms
Cost:                 $
User satisfaction:    thumbs up/down
```

Aggregate into a composite signal; track each dimension independently over time.

---

## The Most Underrated Metric

**Recovery success rate** — the agent's ability to handle:
- Tool failure
- Poor retrieval
- Ambiguous input
- Partial context

...and still produce a useful output.

This often matters more than benchmark accuracy in production.

---

## Architecture That Works

```
User Request
   ↓
Agent Execution
   ↓
Trace Collection (Langfuse / OpenTelemetry)
   ↓
Evaluation Pipeline
   ├── automatic metrics (deterministic)
   ├── LLM-as-judge (sampled)
   ├── heuristics / rules
   ├── human annotation
   └── production outcome metrics
   ↓
Dataset Builder
   ↓
Regression Benchmarks (replay on every deploy)
```

---

## Practical Phased Rollout

### Phase 1
- Langfuse traces
- Cost / latency / error metrics
- Basic feedback buttons (thumbs up/down)
- Capture all prompts and tools

### Phase 2
- Automatic heuristic checks (schema validation, hallucination checks, tool correctness)
- Failure-focused trace sampling

### Phase 3
- LLM judge on sampled traces
- Failure-focused + cost-focused sampling

### Phase 4
- Human annotation queue
- Golden datasets (input → expected behavior → expected tools → expected answer)
- Regression testing on every prompt/model change

---

## Golden Datasets

Eventually needed:
```
input
expected behavior
expected tools called
expected constraints honored
expected answer properties
```

Purpose: replay traces, compare versions, run regression tests — CI/CD for agents.

Without this, every prompt or model change becomes a risk.

---

## Sampling Strategy Overview

> Full detail in [sampling.md](sampling.md).

### The core math

`selected = union(random_sample(20%), high_cost_sample(10%), failure_sample(30%))`

Total ≈ 25–30% depending on overlap. NOT additive. If union reaches 100% → disable
sampling and take everything.

### 16 signal categories

| # | Category | Key criteria |
|---|---|---|
| 1 | Random baseline | `random() < rate`, stratified by model/tenant/workflow |
| 2 | Failure-based | `trace.status == error`, tool errors, schema failures, retries |
| 3 | User dissatisfaction | thumbs_down, regenerate clicks, query repetition, abandonment |
| 4 | High-cost | token > p95, cost > threshold, tool explosion, context > 80% |
| 5 | Long/deep traces | span_count > threshold, depth > threshold, repeated tool pattern |
| 6 | Novelty/outlier | embedding distance, unseen tool sequence, rare intent |
| 7 | Low confidence | judge score variance, weak retrieval, contradictory docs |
| 8 | Version change | prompt/model/retrieval/tool hash changed |
| 9 | Business-critical | critical workflow tag, enterprise tenant, payment/compliance actions |
| 10 | Retrieval failure | low chunk similarity, unreferenced chunks, citation mismatch |
| 11 | Tool-sequence anomaly | policy violation, redundant calls, missing required tool |
| 12 | Recovery behavior | tool_failed AND success, fallback used, retry succeeded |
| 13 | Latency spike | latency > p99, slow span, tool_wait/total > threshold |
| 14 | Judge disagreement | human vs judge delta, multi-judge variance, inconsistent rationale |
| 15 | Active learning | composite priority score: uncertainty × novelty × business_impact |
| 16 | Drift | intent distribution shift, embedding drift, new language/domain |

### Priority scoring (recommended approach)

```
sampling_priority =
    failure_weight + cost_weight + novelty_weight +
    dissatisfaction_weight + risk_weight + judge_disagreement_weight
```

Select top-N traces per day. No percentage math needed.

### Three-layer design

1. **Always include** — critical failures, compliance, security events (no budget cap)
2. **Stratified sampling** — one sample per taxonomy bucket (~60% of daily N)
3. **Exploration** — novelty, drift, active learning top-N (~40% of daily N)

### Trace taxonomy (classify first, sample inside buckets)

```yaml
trace_type: [simple_answer, rag_answer, tool_execution, multi_step_agent,
             recovery_flow, human_escalation]
intent: [support_question, data_analysis, code_generation, workflow_automation]
risk: [low, medium, high]
failure_mode: [retrieval_miss, bad_tool_choice, loop, hallucination,
               schema_error, timeout]
```

---

## Relationship to CEFS Architecture

| Discussion concept | CEFS component |
|---|---|
| Trace collection (Langfuse/OTEL) | llogr + langfuse |
| LLM proxy with cost/token tracking | yallmp |
| LLM-as-judge gates | checkr |
| Sampling / dataset curation | dataimporter |
| Human annotation queue | annotator ← **missing** |
| Judge calibration vs human labels | meta-evaluator ← **missing** |
| Evaluator versioning + A/B | evaluator registry ← **missing** |
| Golden datasets + regression replay | experiment runner ← **missing** |
| Leaderboard / scorecards | aihub |
| Metrics / dashboards | prometheus + grafana |
