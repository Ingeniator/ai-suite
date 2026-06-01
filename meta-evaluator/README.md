# meta-evaluator

CEFS orchestrator — bridges the annotation gap in the Continuous Evaluator Refinement System.

## Role in CEFS

```
dataset-mock  (scored traces from dataimporter)
     │
     │  GET /_mock/datasets/{id}
     │  GET /_mock/files/{file_id}
     ▼
meta-evaluator
     │  POST /api/v0/datasets          (create dataset in annotator-mock)
     │  POST /api/v0/datasets/{id}/files  (upload JSONL)
     │  POST /api/v0/markup_project    (create annotation project)
     │  POST /api/v0/markup_project/{uid}/pools/{pool_id}
     │  POST /api/v0/tasks             (create task, overlap=3)
     │  POST /api/v0/tasks/{uid}/start
     │  GET  /api/v0/tasks/{uid}/state  ← poll until DONE
     │  GET  /api/v0/assignments?task_id={uid}
     ▼
annotator-mock  (simulates 3 annotators: AGREE / DISAGREE / PARTIAL)
     │
     ▼
meta-evaluator computes:
  – agreement_rate   : % items where human majority agrees with AI verdict
  – accuracy         : (TP+TN) / classified
  – precision        : TP / (TP+FP)
  – recall           : TP / (TP+FN)
  – F1               : harmonic mean of precision and recall
  – Cohen's κ        : inter-rater agreement corrected for chance
     ▼
Prometheus  /metrics
/api/v0/summary  (aggregate across all runs)
```

## Run lifecycle

```
QUEUED → FETCHING → SUBMITTING → ANNOTATING → EVALUATING → DONE
                                                          ↘ FAILED
                                                          ↘ CANCELLED
```

## Triggers

### Manual
```bash
# trigger a run for a specific dataset-mock dataset
curl -X POST http://localhost:8020/api/v0/runs \
  -H 'Content-Type: application/json' \
  -d '{"dataset_id": "<id from dataset-mock>", "dataset_name": "my-batch"}'

# poll status
curl http://localhost:8020/api/v0/runs/<run_id>

# summary across all completed runs
curl http://localhost:8020/api/v0/summary
```

### Automatic
The service polls `dataset-mock` every `POLL_INTERVAL_SECONDS` (default 60s) and automatically starts a run for any new dataset not yet seen. On restart the seen-set is empty (in-memory only), so existing datasets will be reprocessed once.

## Environment variables

| Variable                | Default                        | Description                              |
|-------------------------|--------------------------------|------------------------------------------|
| `PORT`                  | `8020`                         | Listen port                              |
| `ROOT_PATH`             | `""`                           | FastAPI root_path for reverse-proxy      |
| `DATASET_MOCK_URL`      | `http://dataset-mock:9100`     | dataset-mock base URL                    |
| `ANNOTATOR_MOCK_URL`    | `http://annotator-mock:8010`   | annotator-mock base URL                  |
| `ANNOTATION_OVERLAP`    | `3`                            | Annotators per item (all 3 in the pool)  |
| `POLL_INTERVAL_SECONDS` | `60`                           | Auto-poller cadence                      |

## Metrics output (per run)

Each trace in the dataset should carry:
- `verdict`: `PASS` or `FAIL` — the AI evaluator's decision
- `score`: float 0–1 — AI confidence

Annotators label each trace as `AGREE` (AI was right) / `DISAGREE` (AI was wrong) / `PARTIAL`.

```
                 Human majority
                 PASS    FAIL
AI verdict PASS [ TP  |  FP ]
           FAIL [ FN  |  TN ]

accuracy      = (TP+TN) / (TP+FP+TN+FN)
precision     = TP / (TP+FP)    # when AI says PASS, how often right?
recall        = TP / (TP+FN)    # of actual PASSes, how many caught?
Cohen's κ     = (Po - Pe) / (1 - Pe)
```

## Quick start

```bash
# local (needs dataset-mock and annotator-mock running)
pip install -r requirements.txt
DATASET_MOCK_URL=http://localhost:9100 \
ANNOTATOR_MOCK_URL=http://localhost:8010 \
python main.py

# via docker-compose (from ai-suite root)
docker compose up meta-evaluator
```

Swagger UI: http://localhost:8020/docs  
Via gateway: http://localhost:8888/meta-evaluator/docs
