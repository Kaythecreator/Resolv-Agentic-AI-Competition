from __future__ import annotations

import threading
import time
import traceback
from copy import deepcopy

import streamlit as st
from langgraph.types import Command

from app.agent_pipeline import (
    begin_timing_capture,
    build_graph,
    consume_local_latency,
    end_timing_capture,
    get_checkpointer,
    has_checkpoint,
    normalize_input_data,
)
from app.db import (
    delete_agent_metrics,
    delete_trace_metrics,
    fetch_agent_metrics,
    fetch_all_complaints,
    fetch_debug_events,
    fetch_trace_metrics,
    init_db,
    log_debug_event,
    upsert_agent_metric,
    upsert_complaint,
    upsert_trace_metric,
)
from app.langsmith_metrics import complaint_tracing, finish_root_trace, langsmith_enabled, start_root_trace, sync_trace_metrics


AGENT_DISPLAY_NAMES = {
    "validate_issue": "Issue Validation",
    "root_cause_analysis": "Root Cause Analysis",
    "severity_assessment": "Severity Assessment",
    "compliance_assessment": "Compliance Assessment",
    "aggregate_results": "Aggregating Results",
    "assign_role": "Team Assignment",
    "review_router": "Review Routing",
    "human_input": "Human Review",
    "auto_proceed": "Auto-Proceeding",
    "create_resolution": "Resolution Planning",
    "create_customer_email": "Drafting Customer Email",
    "reflection_agent": "Compliance Email Review",
}


_STORE_LOCK = threading.RLock()
_COMPLAINTS: dict[str, dict] = {}


def init_store():
    init_db()
    if "_store_loaded" not in st.session_state:
        st.session_state._store_loaded = True
    sync_from_db()


def list_complaints() -> dict:
    sync_from_db()
    with _STORE_LOCK:
        return {complaint_id: _snapshot_entry(entry, complaint_id) for complaint_id, entry in _COMPLAINTS.items()}


def get_complaint(complaint_id: str) -> dict | None:
    sync_from_db()
    _refresh_metrics_if_needed(complaint_id)
    with _STORE_LOCK:
        entry = _COMPLAINTS.get(complaint_id)
        return _snapshot_entry(entry, complaint_id) if entry else None


def get_debug_events_for_complaint(complaint_id: str, limit: int = 50) -> list[dict]:
    return fetch_debug_events(complaint_id, limit=limit)


def add_complaint(complaint_id: str, input_data: dict) -> bool:
    normalized = normalize_input_data(input_data)
    with _STORE_LOCK:
        if complaint_id in _COMPLAINTS:
            return False
        _COMPLAINTS[complaint_id] = {
            "input": normalized,
            "status": "pending",
            "state": {},
            "agent_log": [],
            "agent_metrics": {},
            "trace_ids": [],
            "trace_metrics": {},
            "graph": build_graph(),
            "thread_config": {"configurable": {"thread_id": complaint_id}},
            "error": None,
            "error_traceback": None,
            "trace_id": None,
            "total_latency_seconds": 0.0,
            "total_tokens": None,
            "total_cost": None,
            "metrics_last_synced_at": None,
            "latency_base_seconds": 0.0,
            "segment_started_monotonic": None,
            "segment_started_at_epoch": None,
        }
    save_complaint_to_db(complaint_id)
    _log_event(complaint_id, phase="created", to_status="pending")
    return True


def update_complaint(complaint_id: str, updates: dict):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        entry.update(updates)
    save_complaint_to_db(complaint_id)


def save_complaint_to_db(complaint_id: str):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        record = {
            "complaint_id": complaint_id,
            "input": deepcopy(entry.get("input", {})),
            "status": entry.get("status", "pending"),
            "state": deepcopy(entry.get("state", {})),
            "agent_log": deepcopy(entry.get("agent_log", [])),
            "trace_ids": deepcopy(entry.get("trace_ids", [])),
            "error": entry.get("error"),
            "error_traceback": entry.get("error_traceback"),
            "trace_id": entry.get("trace_id"),
            "total_latency_seconds": entry.get("total_latency_seconds"),
            "total_tokens": entry.get("total_tokens"),
            "total_cost": entry.get("total_cost"),
            "metrics_last_synced_at": entry.get("metrics_last_synced_at"),
        }
    upsert_complaint(record)


def run_pipeline(complaint_id: str, *, resume_payload: dict | None = None):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        graph = entry["graph"]
        config = deepcopy(entry["thread_config"])
        payload = Command(resume=resume_payload) if resume_payload is not None else deepcopy(entry["input"])
        from_status = entry.get("status")
        latency_base = float(entry.get("total_latency_seconds") or 0.0)

    timing_token = begin_timing_capture()
    root_trace = None

    try:
        update_complaint(
            complaint_id,
            {
                "status": "processing",
                "error": None,
                "error_traceback": None,
                "latency_base_seconds": latency_base,
                "segment_started_monotonic": time.perf_counter(),
                "segment_started_at_epoch": time.time(),
            },
        )
        root_trace = start_root_trace(
            complaint_id,
            inputs={"complaint_id": complaint_id, "resume_payload": resume_payload},
            resumed=resume_payload is not None,
        )
        if root_trace:
            trace_id = str(root_trace.trace_id)
            with _STORE_LOCK:
                current = _COMPLAINTS[complaint_id]
                trace_ids = list(current.get("trace_ids", []))
                if trace_id not in trace_ids:
                    trace_ids.append(trace_id)
                current["trace_id"] = trace_id
                current["trace_ids"] = trace_ids
            save_complaint_to_db(complaint_id)
        _log_event(
            complaint_id,
            phase="start" if resume_payload is None else "resume_applied",
            from_status=from_status,
            to_status="processing",
            details={
                "resume_payload": resume_payload,
                "trace_id": str(root_trace.trace_id) if root_trace else None,
                "langsmith_enabled": langsmith_enabled(),
            }
            if resume_payload is not None or root_trace or langsmith_enabled()
            else None,
        )

        with complaint_tracing(root_trace, complaint_id):
            for event in graph.stream(payload, config=config):
                for node_name, node_output in event.items():
                    normalized_output = node_output if isinstance(node_output, dict) else {}
                    display_name = AGENT_DISPLAY_NAMES.get(node_name, node_name)
                    if node_name == "reflection_agent":
                        attempt = normalized_output.get("reflection_attempts")
                        if attempt:
                            display_name = f"{display_name} (attempt {attempt})"

                    with _STORE_LOCK:
                        current = _COMPLAINTS[complaint_id]
                        occurrence_index = (
                            len([item for item in current["agent_log"] if item["node"] == node_name]) + 1
                        )
                        current["agent_log"].append(
                            {
                                "agent": display_name,
                                "node": node_name,
                                "status": "complete",
                                "output": normalized_output,
                                "occurrence_index": occurrence_index,
                                "trace_id": current.get("trace_id"),
                                "completed_at_epoch": time.time(),
                            }
                        )
                        current["state"].update(normalized_output)
                    local_latency = consume_local_latency(node_name)
                    if local_latency is not None:
                        _upsert_agent_metric_record(
                            complaint_id,
                            {
                                "node_name": node_name,
                                "occurrence_index": occurrence_index,
                                "trace_id": _COMPLAINTS[complaint_id].get("trace_id"),
                                "run_id": None,
                                "latency_seconds": local_latency,
                                "total_tokens": None,
                                "total_cost": None,
                                "source": "local_fallback",
                            },
                        )
                    _refresh_live_total_latency(complaint_id)
                    save_complaint_to_db(complaint_id)
                    _log_event(
                        complaint_id,
                        phase="node_complete",
                        node_name=node_name,
                        details={
                            "output_keys": sorted(normalized_output.keys()),
                            "raw_output_was_none": node_output is None,
                            "occurrence_index": occurrence_index,
                            "local_latency_seconds": local_latency,
                        },
                    )

        graph_state = graph.get_state(config)
        if getattr(graph_state, "values", None):
            with _STORE_LOCK:
                _COMPLAINTS[complaint_id]["state"].update(graph_state.values)
            save_complaint_to_db(complaint_id)

        _finalize_total_latency(complaint_id)

        if tuple(getattr(graph_state, "next", ()) or ()) == ("human_input",):
            update_complaint(complaint_id, {"status": "needs_review"})
            finish_root_trace(root_trace, outputs={"status": "needs_review"})
            _sync_metrics_cache(complaint_id)
            _log_event(
                complaint_id,
                phase="interrupt",
                node_name="human_input",
                from_status="processing",
                to_status="needs_review",
                details={
                    "review_reasons": _COMPLAINTS[complaint_id]["state"].get("review_reasons", []),
                },
            )
        else:
            update_complaint(complaint_id, {"status": "complete"})
            finish_root_trace(root_trace, outputs={"status": "complete"})
            _sync_metrics_cache(complaint_id)
            _log_event(complaint_id, phase="complete", from_status="processing", to_status="complete")
    except Exception as exc:
        tb = traceback.format_exc()
        _finalize_total_latency(complaint_id)
        update_complaint(
            complaint_id,
            {"status": "error", "error": str(exc), "error_traceback": tb},
        )
        finish_root_trace(root_trace, outputs={"status": "error"}, error=str(exc))
        _sync_metrics_cache(complaint_id)
        _log_event(
            complaint_id,
            phase="error",
            from_status="processing",
            to_status="error",
            error_class=exc.__class__.__name__,
            error_message=str(exc),
            traceback_text=tb,
        )
    finally:
        with _STORE_LOCK:
            if complaint_id in _COMPLAINTS:
                _COMPLAINTS[complaint_id]["segment_started_monotonic"] = None
                _COMPLAINTS[complaint_id]["latency_base_seconds"] = float(
                    _COMPLAINTS[complaint_id].get("total_latency_seconds") or 0.0
                )
                _COMPLAINTS[complaint_id]["segment_started_at_epoch"] = None
        end_timing_capture(timing_token)


def start_pipeline_thread(complaint_id: str):
    thread = threading.Thread(target=run_pipeline, kwargs={"complaint_id": complaint_id}, daemon=True)
    thread.start()


def resume_pipeline(complaint_id: str, overrides: dict | None = None):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        config = deepcopy(entry["thread_config"])
        status = _effective_status(entry, config)

    resume_payload = {"approved": True, "overrides": overrides or {}}
    checkpoint_exists = has_checkpoint(config)
    _log_event(
        complaint_id,
        phase="resume_requested",
        from_status=status,
        details={"checkpoint_exists": checkpoint_exists, "resume_payload": resume_payload},
    )

    if status != "needs_review":
        raise RuntimeError(f"Complaint {complaint_id} is not awaiting human review.")
    if not checkpoint_exists:
        raise RuntimeError(f"Complaint {complaint_id} has no durable checkpoint to resume.")

    thread = threading.Thread(
        target=run_pipeline,
        kwargs={"complaint_id": complaint_id, "resume_payload": resume_payload},
        daemon=True,
    )
    thread.start()


def restart_complaint(complaint_id: str):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        thread_id = entry["thread_config"]["configurable"]["thread_id"]
        input_data = deepcopy(entry["input"])

    get_checkpointer().delete_thread(thread_id)
    delete_agent_metrics(complaint_id)
    delete_trace_metrics(complaint_id)
    with _STORE_LOCK:
        _COMPLAINTS[complaint_id] = {
            "input": input_data,
            "status": "pending",
            "state": {},
            "agent_log": [],
            "agent_metrics": {},
            "trace_ids": [],
            "trace_metrics": {},
            "graph": build_graph(),
            "thread_config": {"configurable": {"thread_id": complaint_id}},
            "error": None,
            "error_traceback": None,
            "trace_id": None,
            "total_latency_seconds": 0.0,
            "total_tokens": None,
            "total_cost": None,
            "metrics_last_synced_at": None,
            "latency_base_seconds": 0.0,
            "segment_started_monotonic": None,
            "segment_started_at_epoch": None,
        }
    save_complaint_to_db(complaint_id)
    _log_event(complaint_id, phase="restart_requested", to_status="pending")
    start_pipeline_thread(complaint_id)


def sync_from_db():
    records = fetch_all_complaints()
    with _STORE_LOCK:
        for record in records:
            complaint_id = record["complaint_id"]
            if complaint_id in _COMPLAINTS:
                entry = _COMPLAINTS[complaint_id]
                entry["input"] = record["input"]
                entry["status"] = record["status"]
                entry["state"] = record["state"]
                entry["agent_log"] = record["agent_log"]
                entry["agent_metrics"] = fetch_agent_metrics(complaint_id)
                entry["trace_ids"] = record.get("trace_ids", [])
                entry["trace_metrics"] = fetch_trace_metrics(complaint_id)
                entry["error"] = record["error"]
                entry["error_traceback"] = record.get("error_traceback")
                entry["trace_id"] = record.get("trace_id")
                entry["total_latency_seconds"] = record.get("total_latency_seconds")
                entry["total_tokens"] = record.get("total_tokens")
                entry["total_cost"] = record.get("total_cost")
                entry["metrics_last_synced_at"] = record.get("metrics_last_synced_at")
                entry["created_at"] = record.get("created_at")
                entry["updated_at"] = record.get("updated_at")
            else:
                _COMPLAINTS[complaint_id] = _hydrate_record(record)


def _hydrate_record(record: dict) -> dict:
    return {
        "input": record["input"],
        "status": record["status"],
        "state": record["state"],
        "agent_log": record["agent_log"],
        "agent_metrics": fetch_agent_metrics(record["complaint_id"]),
        "trace_ids": record.get("trace_ids", []),
        "trace_metrics": fetch_trace_metrics(record["complaint_id"]),
        "graph": build_graph(),
        "thread_config": {"configurable": {"thread_id": record["complaint_id"]}},
        "error": record["error"],
        "error_traceback": record.get("error_traceback"),
        "trace_id": record.get("trace_id"),
        "total_latency_seconds": record.get("total_latency_seconds") or 0.0,
        "total_tokens": record.get("total_tokens"),
        "total_cost": record.get("total_cost"),
        "metrics_last_synced_at": record.get("metrics_last_synced_at"),
        "latency_base_seconds": record.get("total_latency_seconds") or 0.0,
        "segment_started_monotonic": None,
        "segment_started_at_epoch": None,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _snapshot_entry(entry: dict, complaint_id: str) -> dict:
    config = deepcopy(entry.get("thread_config", {}))
    checkpoint_exists = has_checkpoint(config)
    status = _effective_status(entry, config)
    legacy_non_resumable = status in {"needs_review", "error"} and bool(entry.get("agent_log")) and not checkpoint_exists
    return {
        "input": deepcopy(entry.get("input", {})),
        "status": status,
        "state": deepcopy(entry.get("state", {})),
        "agent_log": deepcopy(entry.get("agent_log", [])),
        "agent_metrics": deepcopy(entry.get("agent_metrics", {})),
        "trace_ids": deepcopy(entry.get("trace_ids", [])),
        "graph": entry.get("graph"),
        "thread_config": config,
        "error": entry.get("error"),
        "error_traceback": entry.get("error_traceback"),
        "trace_id": entry.get("trace_id"),
        "total_latency_seconds": _current_total_latency(entry),
        "total_tokens": entry.get("total_tokens"),
        "total_cost": entry.get("total_cost"),
        "metrics_last_synced_at": entry.get("metrics_last_synced_at"),
        "segment_started_at_epoch": entry.get("segment_started_at_epoch"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "can_resume_review": status == "needs_review" and checkpoint_exists,
        "can_restart": legacy_non_resumable,
        "checkpoint_exists": checkpoint_exists,
    }


def _effective_status(entry: dict, config: dict) -> str:
    status = entry.get("status")
    try:
        graph_state = entry["graph"].get_state(config)
        next_nodes = tuple(getattr(graph_state, "next", ()) or ())
        interrupts = getattr(graph_state, "interrupts", ()) or ()
        if next_nodes == ("human_input",) or interrupts:
            return "needs_review"
    except Exception:
        pass
    return status


def _log_event(
    complaint_id: str,
    *,
    phase: str,
    node_name: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    traceback_text: str | None = None,
):
    with _STORE_LOCK:
        entry = _COMPLAINTS.get(complaint_id)
        thread_id = complaint_id
        if entry:
            thread_id = entry["thread_config"]["configurable"]["thread_id"]
    log_debug_event(
        {
            "complaint_id": complaint_id,
            "thread_id": thread_id,
            "phase": phase,
            "node_name": node_name,
            "from_status": from_status,
            "to_status": to_status,
            "details": details,
            "error_class": error_class,
            "error_message": error_message,
            "traceback": traceback_text,
        }
    )


def _upsert_agent_metric_record(complaint_id: str, metric_record: dict):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        key = (metric_record["node_name"], metric_record["occurrence_index"])
        existing = entry.setdefault("agent_metrics", {}).get(key, {})
        merged = {**existing, **metric_record}
        entry["agent_metrics"][key] = merged
    upsert_agent_metric({"complaint_id": complaint_id, **merged})
    _recompute_metric_totals(complaint_id)


def _recompute_metric_totals(complaint_id: str):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        trace_metrics = entry.get("trace_metrics", {}).values()
        total_latency = 0.0
        saw_latency = False
        total_tokens = 0
        saw_tokens = False
        total_cost = 0.0
        saw_cost = False
        for metric in trace_metrics:
            latency = metric.get("latency_seconds")
            tokens = metric.get("total_tokens")
            cost = metric.get("total_cost")
            if latency is not None:
                total_latency += float(latency)
                saw_latency = True
            if tokens is not None:
                total_tokens += int(tokens)
                saw_tokens = True
            if cost is not None:
                total_cost += float(cost)
                saw_cost = True
        if saw_latency:
            entry["total_latency_seconds"] = total_latency
        entry["total_tokens"] = total_tokens if saw_tokens else None
        entry["total_cost"] = total_cost if saw_cost else None
    save_complaint_to_db(complaint_id)


def _sync_metrics_cache(complaint_id: str, trace_id: str | None = None):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        active_trace_id = trace_id or entry.get("trace_id")
        agent_log = deepcopy(entry.get("agent_log", []))
    if not active_trace_id:
        return
    try:
        synced = sync_trace_metrics(active_trace_id, agent_log=agent_log)
    except Exception as exc:
        _log_event(
            complaint_id,
            phase="metrics_sync_error",
            details={"trace_id": active_trace_id},
            error_class=exc.__class__.__name__,
            error_message=str(exc),
            traceback_text=traceback.format_exc(),
        )
        return

    updated = False
    for metric in synced.get("agent_metrics", []):
        _upsert_agent_metric_record(complaint_id, metric)
        updated = True

    synced_at = synced.get("synced_at")
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        trace_metrics = synced.get("trace_metrics") or {}
        if trace_metrics:
            entry.setdefault("trace_metrics", {})[active_trace_id] = {
                "trace_id": active_trace_id,
                "latency_seconds": trace_metrics.get("latency_seconds"),
                "total_tokens": trace_metrics.get("total_tokens"),
                "total_cost": trace_metrics.get("total_cost"),
                "synced_at": synced_at,
            }
            upsert_trace_metric(
                {
                    "complaint_id": complaint_id,
                    "trace_id": active_trace_id,
                    "latency_seconds": trace_metrics.get("latency_seconds"),
                    "total_tokens": trace_metrics.get("total_tokens"),
                    "total_cost": trace_metrics.get("total_cost"),
                }
            )
        entry["metrics_last_synced_at"] = synced_at or entry.get("metrics_last_synced_at")
    _recompute_metric_totals(complaint_id)
    if updated or synced_at:
        save_complaint_to_db(complaint_id)


def _refresh_metrics_if_needed(complaint_id: str):
    with _STORE_LOCK:
        entry = _COMPLAINTS.get(complaint_id)
        if not entry:
            return
        trace_ids = list(entry.get("trace_ids", []))
        should_resync = entry.get("status") != "processing" and bool(trace_ids)
    if should_resync:
        for trace_id in trace_ids:
            _sync_metrics_cache(complaint_id, trace_id=trace_id)


def _current_total_latency(entry: dict) -> float | None:
    total = float(entry.get("total_latency_seconds") or 0.0)
    started = entry.get("segment_started_monotonic")
    has_langsmith_total = bool(entry.get("metrics_last_synced_at")) and bool(entry.get("trace_id"))
    if entry.get("status") == "processing" and started and not has_langsmith_total:
        total = float(entry.get("latency_base_seconds") or 0.0) + (time.perf_counter() - started)
    return total


def _refresh_live_total_latency(complaint_id: str):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        if entry.get("metrics_last_synced_at") and entry.get("trace_id"):
            return
        entry["total_latency_seconds"] = _current_total_latency(entry)


def _finalize_total_latency(complaint_id: str):
    with _STORE_LOCK:
        entry = _COMPLAINTS[complaint_id]
        if entry.get("metrics_last_synced_at") and entry.get("trace_id"):
            save_needed = True
        else:
            entry["total_latency_seconds"] = _current_total_latency(entry)
            save_needed = True
    if save_needed:
        save_complaint_to_db(complaint_id)
