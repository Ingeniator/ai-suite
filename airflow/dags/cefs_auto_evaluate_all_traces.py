"""Airflow DAG for the first CEFS slice: auto-evaluate every trace window.

Airflow owns cadence and retries. checkr owns trace loading, evaluator execution,
score persistence, and idempotency for the requested window.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pendulum
import requests
from airflow.decorators import dag, task
from airflow.exceptions import AirflowException
from airflow.models import Variable
from airflow.operators.python import get_current_context


DEFAULT_PIPELINES = [
    {
        "pipeline_id": "ai-suite-default.hourly",
        "trace_source_id": "llogr-clickhouse-prod",
        "evaluator_version": "default",
        "enabled": True,
    }
]

TERMINAL_SUCCESS = {"completed", "complete", "succeeded", "success"}
TERMINAL_FAILURE = {"failed", "cancelled", "canceled", "error"}


def _variable_json(name: str, default: Any) -> Any:
    raw = Variable.get(name, default_var=json.dumps(default))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AirflowException(f"Airflow Variable {name} must be valid JSON") from exc


def _checkr_base_url() -> str:
    return Variable.get("CEFS_CHECKR_BASE_URL", default_var="http://checkr:5000").rstrip("/")


def _request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, url, timeout=30, **kwargs)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise AirflowException(f"{method} {url} failed: {response.text}") from exc
    return response.json()


@dag(
    dag_id="cefs_auto_evaluate_all_traces",
    description="Schedule checkr to score all traces for each active CEFS pipeline window.",
    schedule="0 * * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["cefs", "checkr", "evaluation"],
)
def cefs_auto_evaluate_all_traces():
    @task
    def list_active_pipelines() -> list[dict[str, Any]]:
        pipelines = _variable_json("CEFS_AUTO_EVAL_PIPELINES", DEFAULT_PIPELINES)
        return [pipeline for pipeline in pipelines if pipeline.get("enabled", True)]

    @task
    def build_score_request(pipeline: dict[str, Any]) -> dict[str, Any]:
        context = get_current_context()
        data_interval_start = context.get("data_interval_start")
        data_interval_end = context.get("data_interval_end")
        if data_interval_start is None or data_interval_end is None:
            raise AirflowException("Airflow data interval is required")

        pipeline_id = pipeline["pipeline_id"]
        evaluator_version = pipeline["evaluator_version"]
        window_start = data_interval_start.in_timezone("UTC").to_iso8601_string()
        window_end = data_interval_end.in_timezone("UTC").to_iso8601_string()
        idempotency_key = "|".join(
            [
                pipeline_id,
                "score_all_traces",
                window_start,
                window_end,
                evaluator_version,
            ]
        )

        return {
            "pipeline_id": pipeline_id,
            "trace_source_id": pipeline["trace_source_id"],
            "time_window": {"start": window_start, "end": window_end},
            "evaluator_version": evaluator_version,
            "force_rescore": pipeline.get("force_rescore", False),
            "idempotency_key": idempotency_key,
        }

    @task
    def create_score_run(payload: dict[str, Any]) -> dict[str, Any]:
        return _request_json("POST", f"{_checkr_base_url()}/cefs/score-runs", json=payload)

    @task.sensor(poke_interval=60, timeout=60 * 60 * 2, mode="reschedule")
    def wait_for_score_run(score_run: dict[str, Any]) -> bool:
        score_run_id = score_run.get("score_run_id") or score_run.get("id")
        if not score_run_id:
            raise AirflowException("checkr score-run response must include score_run_id or id")

        status = _request_json("GET", f"{_checkr_base_url()}/cefs/score-runs/{score_run_id}")
        state = str(status.get("status", "")).lower()
        if state in TERMINAL_SUCCESS:
            return True
        if state in TERMINAL_FAILURE:
            raise AirflowException(f"checkr score run {score_run_id} ended with status {state}")
        return False

    pipelines = list_active_pipelines()
    requests_ = build_score_request.expand(pipeline=pipelines)
    score_runs = create_score_run.expand(payload=requests_)
    wait_for_score_run.expand(score_run=score_runs)


cefs_auto_evaluate_all_traces()
