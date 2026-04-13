from __future__ import annotations

import os
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from dotenv import load_dotenv


load_dotenv()

LANGSMITH_PROJECT = os.environ.get("LANGSMITH_PROJECT")
LANGSMITH_API_KEY = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
if LANGSMITH_API_KEY:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", LANGSMITH_API_KEY)

try:
    from langsmith import Client
    from langsmith.run_helpers import tracing_context
    from langsmith.run_trees import RunTree
except ImportError:  # pragma: no cover - optional dependency fallback
    Client = None  # type: ignore[assignment]
    tracing_context = None  # type: ignore[assignment]
    RunTree = None  # type: ignore[assignment]


NODE_RUN_NAMES = {
    "validate_issue",
    "root_cause_analysis",
    "severity_assessment",
    "compliance_assessment",
    "aggregate_results",
    "assign_role",
    "review_router",
    "human_input",
    "auto_proceed",
    "create_resolution",
    "create_customer_email",
    "reflection_agent",
}

_CLIENT = Client() if Client and LANGSMITH_API_KEY else None


def langsmith_enabled() -> bool:
    return _CLIENT is not None and bool(LANGSMITH_PROJECT)


def start_root_trace(complaint_id: str, *, inputs: dict[str, Any], resumed: bool) -> Any | None:
    if not langsmith_enabled() or RunTree is None:
        return None
    metadata = {
        "thread_id": complaint_id,
        "complaint_id": complaint_id,
        "resumed": resumed,
    }
    root = RunTree(
        name="Complaint Pipeline",
        run_type="chain",
        inputs=inputs,
        project_name=LANGSMITH_PROJECT,
        tags=["complaint-pipeline"],
        extra={"metadata": metadata},
        ls_client=_CLIENT,
    )
    try:
        root.post()
        return root
    except Exception:
        return None


@contextmanager
def complaint_tracing(root_run: Any | None, complaint_id: str) -> Iterator[None]:
    if not root_run or tracing_context is None:
        yield
        return
    with tracing_context(
        project_name=LANGSMITH_PROJECT,
        parent=root_run,
        metadata={"thread_id": complaint_id, "complaint_id": complaint_id},
        enabled=True,
        client=_CLIENT,
    ):
        yield


def finish_root_trace(root_run: Any | None, *, outputs: dict[str, Any] | None = None, error: str | None = None):
    if not root_run:
        return
    try:
        root_run.end(
            outputs=outputs or {},
            error=error,
            end_time=datetime.now(timezone.utc),
        )
        root_run.patch()
    except Exception:
        return


def sync_trace_metrics(trace_id: str, *, agent_log: list[dict]) -> dict[str, Any]:
    if not langsmith_enabled():
        return {"trace_metrics": None, "agent_metrics": [], "synced_at": None}

    root_run = _load_root_trace(trace_id)
    if root_run is not None:
        runs = _flatten_run_tree(root_run)
    else:
        runs = list(
            _CLIENT.list_runs(
                project_name=LANGSMITH_PROJECT,
                trace_id=trace_id,
                limit=500,
                select=[
                    "id",
                    "name",
                    "run_type",
                    "parent_run_id",
                    "trace_id",
                    "start_time",
                    "end_time",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_cost",
                    "prompt_cost",
                    "completion_cost",
                    "extra",
                ],
            )
        )
        root_run = next((run for run in runs if getattr(run, "parent_run_id", None) is None), None)
    if not runs:
        return {"trace_metrics": None, "agent_metrics": [], "synced_at": None}

    run_map = {str(run.id): run for run in runs}
    children: dict[str | None, list[Any]] = defaultdict(list)
    node_runs_by_name: dict[str, list[Any]] = defaultdict(list)

    for run in runs:
        parent_id = str(run.parent_run_id) if getattr(run, "parent_run_id", None) else None
        children[parent_id].append(run)
        if getattr(run, "name", None) in NODE_RUN_NAMES:
            node_runs_by_name[run.name].append(run)

    for grouped in node_runs_by_name.values():
        grouped.sort(key=_run_sort_key)

    subtree_cache: dict[str, tuple[int | None, float | None]] = {}

    def subtree_usage(run_id: str) -> tuple[int | None, float | None]:
        if run_id in subtree_cache:
            return subtree_cache[run_id]
        run = run_map[run_id]
        total_tokens, total_cost = _direct_run_usage(run)
        if total_tokens is None:
            token_sum = 0
            saw_tokens = False
            for child in children.get(run_id, []):
                child_tokens, _ = subtree_usage(str(child.id))
                if child_tokens is not None:
                    token_sum += child_tokens
                    saw_tokens = True
            total_tokens = token_sum if saw_tokens else None
        if total_cost is None:
            cost_sum = 0.0
            saw_cost = False
            for child in children.get(run_id, []):
                _, child_cost = subtree_usage(str(child.id))
                if child_cost is not None:
                    cost_sum += child_cost
                    saw_cost = True
            total_cost = cost_sum if saw_cost else None
        subtree_cache[run_id] = (total_tokens, total_cost)
        return subtree_cache[run_id]

    logs_by_node: dict[str, list[dict]] = defaultdict(list)
    for log in agent_log:
        if log.get("trace_id") == trace_id:
            logs_by_node[log["node"]].append(log)

    agent_metrics: list[dict[str, Any]] = []
    for node_name, logs in logs_by_node.items():
        trace_runs = node_runs_by_name.get(node_name, [])
        for log, run in zip(logs, trace_runs):
            latency_seconds = _run_latency(run)
            total_tokens, total_cost = subtree_usage(str(run.id))
            agent_metrics.append(
                {
                    "node_name": node_name,
                    "occurrence_index": log["occurrence_index"],
                    "trace_id": trace_id,
                    "run_id": str(run.id),
                    "latency_seconds": latency_seconds,
                    "total_tokens": total_tokens,
                    "total_cost": total_cost,
                    "source": "langsmith",
                }
            )

    trace_metrics = None
    if root_run is not None:
        trace_tokens, trace_cost = subtree_usage(str(root_run.id))
        if trace_tokens is None or trace_cost is None:
            llm_tokens, llm_cost = _sum_llm_run_usage(runs)
            trace_tokens = trace_tokens if trace_tokens is not None else llm_tokens
            trace_cost = trace_cost if trace_cost is not None else llm_cost
        trace_metrics = {
            "trace_id": trace_id,
            "latency_seconds": _run_latency(root_run),
            "total_tokens": trace_tokens,
            "total_cost": trace_cost,
        }

    return {
        "trace_metrics": trace_metrics,
        "agent_metrics": agent_metrics,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_root_trace(trace_id: str) -> Any | None:
    try:
        return _CLIENT.read_run(trace_id, load_child_runs=True)
    except Exception:
        return None


def _flatten_run_tree(root_run: Any) -> list[Any]:
    flattened: list[Any] = []

    def visit(run: Any):
        flattened.append(run)
        for child in getattr(run, "child_runs", []) or []:
            visit(child)

    visit(root_run)
    return flattened


def _run_sort_key(run: Any):
    return (
        getattr(run, "start_time", None) or datetime.min.replace(tzinfo=timezone.utc),
        str(getattr(run, "id", "")),
    )


def _run_latency(run: Any) -> float | None:
    start = getattr(run, "start_time", None)
    end = getattr(run, "end_time", None)
    if not start or not end:
        return None
    return max((end - start).total_seconds(), 0.0)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direct_run_usage(run: Any) -> tuple[int | None, float | None]:
    total_tokens = _coerce_int(getattr(run, "total_tokens", None))
    if total_tokens is None:
        prompt_tokens = _coerce_int(getattr(run, "prompt_tokens", None))
        completion_tokens = _coerce_int(getattr(run, "completion_tokens", None))
        if prompt_tokens is not None or completion_tokens is not None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    total_cost = _coerce_float(getattr(run, "total_cost", None))
    if total_cost is None:
        prompt_cost = _coerce_float(getattr(run, "prompt_cost", None))
        completion_cost = _coerce_float(getattr(run, "completion_cost", None))
        if prompt_cost is not None or completion_cost is not None:
            total_cost = float(prompt_cost or 0.0) + float(completion_cost or 0.0)

    extra = getattr(run, "extra", None) or {}
    metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}
    usage_metadata = metadata.get("usage_metadata", {}) if isinstance(metadata, dict) else {}
    if total_tokens is None and isinstance(usage_metadata, dict):
        input_tokens = _coerce_int(usage_metadata.get("input_tokens"))
        output_tokens = _coerce_int(usage_metadata.get("output_tokens"))
        total_usage_tokens = _coerce_int(usage_metadata.get("total_tokens"))
        if total_usage_tokens is not None:
            total_tokens = total_usage_tokens
        elif input_tokens is not None or output_tokens is not None:
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return total_tokens, total_cost


def _sum_llm_run_usage(runs: list[Any]) -> tuple[int | None, float | None]:
    total_tokens = 0
    saw_tokens = False
    total_cost = 0.0
    saw_cost = False
    for run in runs:
        if getattr(run, "run_type", None) != "llm":
            continue
        run_tokens, run_cost = _direct_run_usage(run)
        if run_tokens is not None:
            total_tokens += run_tokens
            saw_tokens = True
        if run_cost is not None:
            total_cost += run_cost
            saw_cost = True
    return (total_tokens if saw_tokens else None, total_cost if saw_cost else None)
