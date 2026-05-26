# annotator-mock

Mock annotation service for the CEFS loop.  
Implements a subset of the annotation platform API.

## Role in CEFS

```
dataimporter
    │ 1. POST /api/v0/datasets            (create dataset)
    │ 2. POST /api/v0/datasets/{id}/files  (upload scored traces JSONL)
    │ 3. POST /api/v0/markup_project       (create project)
    │ 4. POST /api/v0/markup_project/{uid}/pools/{pool_id}  (assign annotators)
    │ 5. POST /api/v0/tasks                (create task linked to dataset)
    │ 6. POST /api/v0/tasks/{uid}/start    (begin annotation)
    ▼
annotator-mock
    simulates 3 annotators labelling each trace:
      AGREE | DISAGREE | PARTIAL  (default 70/20/10 %)
    ▼
meta-evaluator
    GET /api/v0/assignments?task_id={id}   → human ground-truth labels
    GET /api/v0/statistics/task/{id}       → completion stats
```

## Hardcoded annotator pool

Pool ID: **`a1b2c3d4-e5f6-7890-abcd-ef1234567890`**  
Always available.  Members: Anna Reviewer, Boris Analyst, Carla Expert.

```
GET /api/v0/pools/hardcoded   → {"pool_id": "a1b2c3d4-..."}
GET /api/v0/pools             → list all pools
```

## Quick start

```bash
# local
pip install -r requirements.txt
python main.py

# docker
docker build -t annotator-mock .
docker run -p 8010:8010 annotator-mock

# docker-compose (from ai-suite root)
docker compose up annotator-mock
```

Swagger UI: http://localhost:8010/docs

## Environment variables

| Variable              | Default | Description                                  |
|-----------------------|---------|----------------------------------------------|
| `PORT`                | `8010`  | Listen port                                  |
| `ANNOTATION_SPEED`    | `2`     | Items annotated per second (per annotator)   |
| `LABEL_W_AGREE`       | `70`    | Weight for AGREE label                       |
| `LABEL_W_DISAGREE`    | `20`    | Weight for DISAGREE label                    |
| `LABEL_W_PARTIAL`     | `10`    | Weight for PARTIAL label                     |

## End-to-end example

```bash
BASE=http://localhost:8010

# 1. Create dataset
DS=$(curl -s -X POST $BASE/api/v0/datasets \
  -H 'Content-Type: application/json' \
  -d '{"name":"cefs-batch-001","access":"organization"}' | jq -r .id)

# 2. Upload scored traces (one JSON object per line)
curl -s -X POST $BASE/api/v0/datasets/$DS/files \
  -F 'file=@/tmp/scored_traces.jsonl;type=application/json'

# 3. Create project
PROJ=$(curl -s -X POST $BASE/api/v0/markup_project \
  -H 'Content-Type: application/json' \
  -d '{"name":"CEFS round 1"}' | jq -r .uid)

# 4. Assign hardcoded pool
POOL=a1b2c3d4-e5f6-7890-abcd-ef1234567890
curl -s -X POST $BASE/api/v0/markup_project/$PROJ/pools/$POOL

# 5. Create task (overlap=3 → all 3 annotators label every item)
TASK=$(curl -s -X POST $BASE/api/v0/tasks \
  -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PROJ\",\"dataset_id\":\"$DS\",\"overlap\":3,\"name\":\"round-1\"}" | jq -r .uid)

# 6. Start
curl -s -X POST $BASE/api/v0/tasks/$TASK/start | jq .state

# 7. Poll until DONE
until [ "$(curl -s $BASE/api/v0/tasks/$TASK/state | jq -r .state)" = "DONE" ]; do
  sleep 1; echo "waiting..."
done

# 8. Get results
curl -s "$BASE/api/v0/assignments?task_id=$TASK" | jq '.items[].items[].result'

# OR export as CSV
curl -s "$BASE/api/v0/datasets/$DS/export?format=csv" -o results.csv
```

## TUI — workflow overseer

```bash
pip install -r requirements-tui.txt    # once

python3 tui.py                         # connect to localhost:8010
python3 tui.py --base http://host:8010 # remote service
```

```
 annotator-mock — 2 projects  2 tasks  running ⟳ 1  done ✓ 1
┌─ PROJECTS ──────────────┐ ┌─ TASKS ─────────────────────────────────────────┐
│  Project   Tasks  Pools  │ │  Name       State    Progress        Overlap     │
│  ▶ CEFS r1  1      1    │ │  ▶ round-1  DONE     ██████████ 100%  ×3         │
│    CEFS r2  1      1    │ │    round-2  INITIAL  ──────────       ×3         │
└─────────────────────────┘ └─────────────────────────────────────────────────┘
┌─ RESULTS ─────────────────────────────────────────────────────────────────────┐
│ Task: round-1  State: DONE  Progress: 3/3 ████████████ 100%                   │
│                                                                               │
│ Annotator summary                                                             │
│   Anna Reviewer      AGREE 1  DISAGREE 1  PARTIAL 1                          │
│   Boris Analyst      AGREE 3  DISAGREE 0  PARTIAL 0                          │
│   Carla Expert       AGREE 3  DISAGREE 0  PARTIAL 0                          │
│                                                                               │
│ Per-item consensus                                                            │
│   tr-001 score=0.42 [FAIL]  A=2 D=1 P=0  → AGREE                            │
│   tr-002 score=0.91 [PASS]  A=3 D=0 P=0  → AGREE                            │
│   tr-003 score=0.55 [PASS]  A=2 D=0 P=1  → AGREE                            │
└───────────────────────────────────────────────────────────────────────────────┘
 q Quit   r Refresh
```

**Keys:** `Tab`/`Shift+Tab` — switch panel · `↑↓` — navigate · `Enter` — pin selection · `r` — force refresh · `q` — quit

Auto-refreshes every 2 seconds; `Enter` on a project filters tasks to that project; `Enter` on a task loads full annotation results in the bottom panel.

## Result format

Each assignment item carries:

```json
{
  "label":      "AGREE | DISAGREE | PARTIAL",
  "confidence": 0.85,
  "note":       "optional free-text explanation"
}
```

Original trace fields are passed through with a `data_` prefix in CSV export.
