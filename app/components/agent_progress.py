from __future__ import annotations

import os
import html
import json

import streamlit as st
import streamlit.components.v1 as components

from app.agent_pipeline import get_rag_debug_payload
from app.state_store import AGENT_DISPLAY_NAMES, get_complaint, get_debug_events_for_complaint, restart_complaint, resume_pipeline
from app.taxonomy_helpers import (
    get_internal_teams,
    get_issues,
    get_priorities,
    get_products,
    get_sla_options,
    get_sub_issues,
    get_sub_products,
)
from app.ui.icons import phosphor_icon


PIPELINE_ROWS = [
    ["validate_issue"],
    ["root_cause_analysis", "severity_assessment", "compliance_assessment"],
    ["aggregate_results"],
    ["assign_role"],
    ["review_router"],
    ["human_input", "auto_proceed"],
    ["create_resolution"],
]
LOOP_START_NODE = "create_resolution"

OUTPUT_LABELS = {
    "valid_product": "Product",
    "valid_sub_product": "Sub-Product",
    "valid_issue": "Issue",
    "valid_sub_issue": "Sub-Issue",
    "confidence": "Confidence",
    "root_cause": "Root Cause",
    "severity": "Severity",
    "severity_explanation": "Severity Explanation",
    "compliance": "Compliance",
    "compliance_explanation": "Compliance Explanation",
    "applicable_regulation": "Regulation",
    "citation": "Citation",
    "combined_results": "Summary",
    "needs_human_review": "Needs Human Review",
    "review_reasons": "Review Reasons",
    "team": "Team",
    "team_explanation": "Team Explanation",
    "priority": "Priority",
    "sla_days": "SLA Days",
    "sla_deadline": "SLA Deadline",
    "remediation_steps": "Resolution Plan",
    "preventative_recommendations": "Preventative Recommendations",
    "customer_email": "Customer Email",
    "reflection_feedback": "Reflection Feedback",
    "reflection_score": "Reflection Score",
    "reflection_passed": "Reflection Passed",
    "reflection_attempts": "Reflection Attempts",
}


def render_detail_view(complaint_id: str):
    _inject_icon_styles()
    if "debug_console_logs" not in st.session_state:
        st.session_state.debug_console_logs = os.environ.get("STREAMLIT_DEBUG_PAYLOADS", "").lower() in {"1", "true", "yes", "on"}

    initial_entry = get_complaint(complaint_id)
    run_every = 2 if initial_entry and initial_entry["status"] == "processing" else None

    @st.fragment(run_every=run_every)
    def _detail_fragment():
        entry = get_complaint(complaint_id)
        if not entry:
            st.error("Complaint not found.")
            return

        if entry["status"] != "needs_review":
            st.session_state.pop(f"review_action_in_flight_{complaint_id}", None)

        header_left, header_right = st.columns([6, 2])
        with header_left:
            if st.button("Back to Dashboard"):
                st.session_state.selected_complaint = None
                st.rerun()
        with header_right:
            st.toggle("Debug console logs", key="debug_console_logs")

        st.subheader(f"Complaint {complaint_id}")
        st.markdown(_complaint_metrics_caption(entry), unsafe_allow_html=True)
        st.caption(f"Status: {entry['status']}")

        action_message = st.session_state.get(f"review_action_in_flight_{complaint_id}")
        if action_message and entry["status"] in {"processing", "pending"}:
            st.info(action_message)

        if entry["status"] == "needs_review":
            _render_review_panel(complaint_id, entry)
        elif entry.get("can_restart"):
            _render_restart_panel(complaint_id, entry)

        left, right = st.columns([2, 3])
        with left:
            _render_outputs(entry)
        with right:
            _render_progress(entry)

        if st.session_state.get("debug_console_logs"):
            _log_rag_context_to_browser(complaint_id, entry)

    _detail_fragment()


def _render_review_panel(complaint_id: str, entry: dict):
    review_node = entry.get("active_review_node") or "human_input"
    if review_node == "final_approval":
        _render_final_approval_panel(complaint_id, entry)
        return

    reasons = entry["state"].get("review_reasons", [])
    with st.warning("This complaint requires human review before proceeding."):
        if reasons:
            st.write("Reasons: " + ", ".join(reasons))
        if not entry.get("can_resume_review", False):
            st.write("This complaint was created under the legacy checkpoint flow and cannot be resumed safely.")
            if st.button("Restart Run", key=f"detail_restart_review_{complaint_id}", use_container_width=True):
                restart_complaint(complaint_id)
                st.rerun()
            return
        btn1, btn2 = st.columns(2)
        if btn1.button("Approve", key=f"detail_approve_{complaint_id}", use_container_width=True):
            st.session_state[f"review_action_in_flight_{complaint_id}"] = "Applying approval and resuming complaint..."
            resume_pipeline(complaint_id)
            st.rerun()
        if btn2.button("Edit", key=f"detail_edit_{complaint_id}", use_container_width=True):
            st.session_state[f"editing_detail_{complaint_id}"] = True
            st.rerun()

    if st.session_state.get(f"editing_detail_{complaint_id}"):
        state = entry["state"]
        with st.container(border=True):
            current_product = state.get("valid_product") or entry["input"].get("product") or ""
            product_options = get_products()
            product_index = product_options.index(current_product) if current_product in product_options else 0
            new_product = st.selectbox("Product", product_options, index=product_index, key=f"detail_product_{complaint_id}")

            sub_product_options = get_sub_products(new_product)
            current_sub_product = state.get("valid_sub_product") or entry["input"].get("sub_product") or ""
            sub_product_index = sub_product_options.index(current_sub_product) if current_sub_product in sub_product_options else 0
            new_sub_product = st.selectbox(
                "Sub-Product",
                sub_product_options,
                index=sub_product_index,
                key=f"detail_sub_product_{complaint_id}",
            )

            issue_options = get_issues(new_product, new_sub_product)
            current_issue = state.get("valid_issue") or entry["input"].get("issue") or ""
            issue_index = issue_options.index(current_issue) if current_issue in issue_options else 0
            new_issue = st.selectbox("Issue", issue_options, index=issue_index, key=f"detail_issue_{complaint_id}")

            sub_issue_options = get_sub_issues(new_product, new_sub_product, new_issue)
            current_sub_issue = state.get("valid_sub_issue") or entry["input"].get("sub_issue") or ""
            sub_issue_index = sub_issue_options.index(current_sub_issue) if current_sub_issue in sub_issue_options else 0
            new_sub_issue = st.selectbox(
                "Sub-Issue",
                sub_issue_options,
                index=sub_issue_index,
                key=f"detail_sub_issue_{complaint_id}",
            )

            severity_options = list(range(1, 11))
            current_severity = int(state.get("severity", 5) or 5)
            new_severity = st.selectbox(
                "Severity",
                severity_options,
                index=severity_options.index(current_severity),
                key=f"detail_severity_{complaint_id}",
            )
            current_compliance = int(state.get("compliance", 5) or 5)
            new_compliance = st.selectbox(
                "Compliance",
                severity_options,
                index=severity_options.index(current_compliance),
                key=f"detail_compliance_{complaint_id}",
            )

            team_options = get_internal_teams()
            current_team = state.get("team", "") or team_options[0]
            team_index = team_options.index(current_team) if current_team in team_options else 0
            new_team = st.selectbox("Team", team_options, index=team_index, key=f"detail_team_{complaint_id}")

            priority_options = get_priorities()
            current_priority = state.get("priority", "") or priority_options[0]
            priority_index = priority_options.index(current_priority) if current_priority in priority_options else 0
            new_priority = st.selectbox(
                "Priority",
                priority_options,
                index=priority_index,
                key=f"detail_priority_{complaint_id}",
            )

            sla_options = get_sla_options()
            current_sla = int(state.get("sla_days", sla_options[0]) or sla_options[0])
            sla_index = sla_options.index(current_sla) if current_sla in sla_options else 0
            new_sla = st.selectbox("SLA (business days)", sla_options, index=sla_index, key=f"detail_sla_{complaint_id}")
            c1, c2 = st.columns(2)
            if c1.button("Approve with Changes", key=f"detail_confirm_{complaint_id}", use_container_width=True):
                overrides = {}
                if new_product != state.get("valid_product"):
                    overrides["valid_product"] = new_product
                if new_sub_product != state.get("valid_sub_product"):
                    overrides["valid_sub_product"] = new_sub_product
                if new_issue != state.get("valid_issue"):
                    overrides["valid_issue"] = new_issue
                if new_sub_issue != state.get("valid_sub_issue"):
                    overrides["valid_sub_issue"] = new_sub_issue
                if new_severity != state.get("severity"):
                    overrides["severity"] = new_severity
                if new_compliance != state.get("compliance"):
                    overrides["compliance"] = new_compliance
                if new_team != state.get("team"):
                    overrides["team"] = new_team
                if new_priority != state.get("priority"):
                    overrides["priority"] = new_priority
                if new_sla != state.get("sla_days"):
                    overrides["sla_days"] = new_sla
                st.session_state[f"review_action_in_flight_{complaint_id}"] = "Applying changes and resuming complaint..."
                resume_pipeline(complaint_id, overrides=overrides or None)
                st.session_state.pop(f"editing_detail_{complaint_id}", None)
                st.rerun()
            if c2.button("Cancel", key=f"detail_cancel_{complaint_id}", use_container_width=True):
                st.session_state.pop(f"editing_detail_{complaint_id}", None)
                st.rerun()


def _render_final_approval_panel(complaint_id: str, entry: dict):
    reasons = entry["state"].get("final_approval_reasons", [])
    with st.warning("This complaint requires final outbound approval before completion."):
        if reasons:
            st.write("Reasons: " + ", ".join(reasons))
        btn1, btn2 = st.columns(2)
        if btn1.button("Approve Final Response", key=f"detail_final_approve_{complaint_id}", use_container_width=True):
            st.session_state[f"review_action_in_flight_{complaint_id}"] = "Approving final response..."
            resume_pipeline(complaint_id)
            st.rerun()
        if btn2.button("Edit Response", key=f"detail_final_edit_{complaint_id}", use_container_width=True):
            st.session_state[f"editing_final_detail_{complaint_id}"] = True
            st.rerun()

    if st.session_state.get(f"editing_final_detail_{complaint_id}"):
        state = entry["state"]
        with st.container(border=True):
            new_resolution = st.text_area(
                "Resolution Plan",
                value=state.get("remediation_steps", "") or "",
                height=160,
                key=f"detail_final_resolution_{complaint_id}",
            )
            new_email = st.text_area(
                "Customer Email",
                value=state.get("customer_email", "") or "",
                height=260,
                key=f"detail_final_email_{complaint_id}",
            )
            c1, c2 = st.columns(2)
            if c1.button("Approve with Final Edits", key=f"detail_final_confirm_{complaint_id}", use_container_width=True):
                overrides = {}
                if new_resolution != state.get("remediation_steps"):
                    overrides["remediation_steps"] = new_resolution
                if new_email != state.get("customer_email"):
                    overrides["customer_email"] = new_email
                st.session_state[f"review_action_in_flight_{complaint_id}"] = "Applying final edits and resuming complaint..."
                resume_pipeline(complaint_id, overrides=overrides or None)
                st.session_state.pop(f"editing_final_detail_{complaint_id}", None)
                st.rerun()
            if c2.button("Cancel", key=f"detail_final_cancel_{complaint_id}", use_container_width=True):
                st.session_state.pop(f"editing_final_detail_{complaint_id}", None)
                st.rerun()


def _render_restart_panel(complaint_id: str, entry: dict):
    with st.warning("This complaint cannot be resumed because it has no durable LangGraph checkpoint."):
        if entry.get("error"):
            st.write(f"Last error: {entry['error']}")
        if st.button("Restart Run", key=f"detail_restart_error_{complaint_id}", use_container_width=True):
            restart_complaint(complaint_id)
            st.rerun()


def _render_progress(entry: dict):
    agent_log = entry.get("agent_log", [])
    completed_nodes = {item["node"] for item in agent_log if item["status"] == "complete"}
    next_nodes = set()
    try:
        graph_state = entry["graph"].get_state(entry["thread_config"])
        next_nodes = set(graph_state.next or ())
    except Exception:
        next_nodes = set()

    visible_rows = _visible_rows(entry, completed_nodes, next_nodes)
    for idx, row in enumerate(visible_rows):
        for node_idx, node in enumerate(row):
            _render_step_block(node, entry, completed_nodes, next_nodes)
            if node_idx < len(row) - 1:
                st.markdown('<div class="agent-parallel-gap"></div>', unsafe_allow_html=True)
        if idx < len(visible_rows) - 1:
            st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)
    if _should_render_email_sequence(entry, completed_nodes, next_nodes):
        if visible_rows:
            st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)
        _render_email_sequence(entry, next_nodes)


def _render_step_block(node: str, entry: dict, completed_nodes: set[str], next_nodes: set[str]):
    if node == "human_input" and not entry["state"].get("needs_human_review"):
        return
    if node == "auto_proceed" and entry["state"].get("needs_human_review"):
        return

    is_current = node in next_nodes and entry["status"] in {"processing", "needs_review"}
    status_icon = phosphor_icon("hourglass-low", size=16)
    if node in completed_nodes:
        status_icon = phosphor_icon("check-circle", size=16)
    elif is_current:
        status_icon = f'<span class="agent-spinner">{phosphor_icon("circle-notch", size=16)}</span>'
    elif entry["status"] == "error":
        status_icon = phosphor_icon("x-circle", size=16)

    label = AGENT_DISPLAY_NAMES.get(node, node)
    matching = [item for item in entry["agent_log"] if item["node"] == node]
    if node in completed_nodes:
        latest = matching[-1]["output"] if matching else {}
        occurrence_index = matching[-1].get("occurrence_index") if matching else None
        metric = entry.get("agent_metrics", {}).get((node, occurrence_index)) if occurrence_index else None
        st.markdown(_completed_step_html(label, latest, metric=metric), unsafe_allow_html=True)
    else:
        _render_in_progress_step(label, status_icon, is_current, node, entry)


def _visible_rows(entry: dict, completed_nodes: set[str], next_nodes: set[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in PIPELINE_ROWS:
        if LOOP_START_NODE in row or "reflection_agent" in row:
            break
        filtered = [node for node in row if not (node == "human_input" and not entry["state"].get("needs_human_review"))]
        filtered = [node for node in filtered if not (node == "auto_proceed" and entry["state"].get("needs_human_review"))]
        if not filtered:
            continue
        completed_here = [node for node in filtered if node in completed_nodes]
        active_here = [node for node in filtered if node in next_nodes and entry["status"] in {"processing", "needs_review"}]
        if completed_here or active_here:
            rows.append(filtered)
            if active_here or len(completed_here) < len(filtered):
                break
        elif entry["status"] in {"needs_review", "error"} and rows:
            break
    return rows


def _should_render_email_sequence(entry: dict, completed_nodes: set[str], next_nodes: set[str]) -> bool:
    email_nodes = {"create_resolution", "create_customer_email", "reflection_agent", "final_approval"}
    return bool(email_nodes & completed_nodes) or bool(email_nodes & next_nodes)


def _render_email_sequence(entry: dict, next_nodes: set[str]):
    agent_log = entry.get("agent_log", [])
    resolution_logs = [item for item in agent_log if item["node"] == "create_resolution"]
    draft_logs = [item for item in agent_log if item["node"] == "create_customer_email"]
    review_logs = [item for item in agent_log if item["node"] == "reflection_agent"]
    final_logs = [item for item in agent_log if item["node"] == "final_approval"]

    if resolution_logs:
        st.markdown(
            _completed_step_html(
                "Resolution Planning",
                resolution_logs[-1]["output"],
                metric=_metric_for_log(entry, resolution_logs[-1]),
            ),
            unsafe_allow_html=True,
        )
        if draft_logs or review_logs or "create_customer_email" in next_nodes or "reflection_agent" in next_nodes:
            st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)
    elif "create_resolution" in next_nodes:
        _render_in_progress_step(
            "Resolution Planning",
            f'<span class="agent-spinner">{phosphor_icon("circle-notch", size=16)}</span>',
            True,
            "create_resolution",
            entry,
        )
        return

    pair_count = max(len(draft_logs), len(review_logs))
    for idx in range(pair_count):
        draft_label = "Drafted Customer Email" if idx == 0 else f"Drafted Customer Email #{idx + 1}"
        if idx < len(draft_logs):
            st.markdown(
                _completed_step_html(draft_label, draft_logs[idx]["output"], metric=_metric_for_log(entry, draft_logs[idx])),
                unsafe_allow_html=True,
            )
            if idx < len(review_logs) or idx < pair_count - 1 or "reflection_agent" in next_nodes:
                st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)

        if idx < len(review_logs):
            review_output = review_logs[idx]["output"]
            passed = bool(review_output.get("reflection_passed"))
            review_label = "Succeeded Compliance Email Review" if passed else "Failed Compliance Email Review"
            icon_name = "check-circle" if passed else "x-circle"
            st.markdown(
                _completed_step_html(review_label, review_output, icon_name=icon_name, metric=_metric_for_log(entry, review_logs[idx])),
                unsafe_allow_html=True,
            )
            if idx < pair_count - 1 or "create_customer_email" in next_nodes:
                st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)

    if "create_customer_email" in next_nodes and len(draft_logs) == len(review_logs):
        next_attempt = len(draft_logs) + 1
        draft_label = "Drafted Customer Email" if next_attempt == 1 else f"Drafted Customer Email #{next_attempt}"
        _render_in_progress_step(
            draft_label,
            f'<span class="agent-spinner">{phosphor_icon("circle-notch", size=16)}</span>',
            True,
            "create_customer_email",
            entry,
        )
    elif "reflection_agent" in next_nodes and len(draft_logs) > len(review_logs):
        _render_in_progress_step(
            "Running Compliance Email Review",
            f'<span class="agent-spinner">{phosphor_icon("circle-notch", size=16)}</span>',
            True,
            "reflection_agent",
            entry,
            raw_label=True,
        )
        return

    if final_logs:
        if pair_count or resolution_logs:
            st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)
        st.markdown(
            _completed_step_html(
                "Final Outbound Approval",
                final_logs[-1]["output"],
                metric=_metric_for_log(entry, final_logs[-1]),
            ),
            unsafe_allow_html=True,
        )
    elif "final_approval" in next_nodes:
        if pair_count or resolution_logs:
            st.markdown('<div class="agent-connector"></div>', unsafe_allow_html=True)
        _render_in_progress_step(
            "Awaiting Final Outbound Approval",
            f'<span class="agent-spinner">{phosphor_icon("circle-notch", size=16)}</span>',
            True,
            "final_approval",
            entry,
            raw_label=True,
        )


def _render_agent_output(output: dict):
    if not output:
        st.caption("No state update emitted.")
        return
    lines: list[str] = []
    for key, value in output.items():
        label = OUTPUT_LABELS.get(key, key.replace("_", " ").title())
        rendered_value = _format_output_value(key, value)
        lines.append(f"**{label}:** {rendered_value}")
    st.markdown("\n\n".join(lines))


def _render_agent_output_html(output: dict) -> str:
    if not output:
        return '<div class="agent-output-empty">No state update emitted.</div>'
    lines: list[str] = []
    for key, value in output.items():
        label = OUTPUT_LABELS.get(key, key.replace("_", " ").title())
        rendered_value = _format_output_value(key, value)
        lines.append(
            f'<div class="agent-output-line"><span class="agent-output-label">{label}:</span> '
            f'<span class="agent-output-value">{rendered_value}</span></div>'
        )
    return "".join(lines)


def _completed_step_html(label: str, output: dict, *, icon_name: str = "check-circle", metric: dict | None = None) -> str:
    icon_color = "#43A047" if icon_name == "check-circle" else "#E53935" if icon_name == "x-circle" else "currentColor"
    check = phosphor_icon(icon_name, size=16, color=icon_color)
    caret = phosphor_icon("caret-right", size=14)
    metric_html = ""
    if metric and metric.get("latency_seconds") is not None:
        metric_html = (
            f'<span class="agent-step-metric">{phosphor_icon("clock", size=12)}'
            f'<span>{_format_latency(metric["latency_seconds"])}</span></span>'
        )
    body = _render_agent_output_html(output)
    return (
        '<details class="agent-completed-step">'
        f'<summary><span class="agent-completed-title">{check}<span>{label}</span>'
        f'<span class="agent-completed-caret">{caret}</span>{metric_html}</span></summary>'
        f'<div class="agent-completed-body">{body}</div>'
        "</details>"
    )


def _render_in_progress_step(label: str, status_icon: str, is_current: bool, node: str, entry: dict, *, raw_label: bool = False):
    if raw_label:
        state_label = ""
    elif is_current and node == "human_input" and entry["status"] == "needs_review":
        state_label = "Awaiting Review"
    else:
        state_label = "Running" if is_current else "Pending"
    text = label if raw_label or not state_label else f"{state_label} {label}"
    text_class = "agent-shimmer-text" if is_current else ""
    st.markdown(
        f'<div class="agent-step-title in-progress">{status_icon}<span class="{text_class}">{text}</span></div>',
        unsafe_allow_html=True,
    )


def _format_output_value(key: str, value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and key == "confidence":
        return f"{value:.0%}"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value) if value else "None"
    if isinstance(value, str):
        return value.replace("\n", "<br>")
    return str(value)


def _metric_for_log(entry: dict, log: dict) -> dict | None:
    return entry.get("agent_metrics", {}).get((log["node"], log.get("occurrence_index")))


def _complaint_metrics_caption(entry: dict) -> str:
    has_final_langsmith_metrics = entry.get("status") in {"complete", "error"} and bool(entry.get("metrics_last_synced_at"))
    latency = _format_latency(entry.get("total_latency_seconds")) if has_final_langsmith_metrics else "—"
    tokens = _format_tokens(entry.get("total_tokens")) if has_final_langsmith_metrics else "—"
    cost = _format_cost(entry.get("total_cost")) if has_final_langsmith_metrics else "—"
    return (
        '<div class="complaint-metrics-caption">'
        f'<span>{phosphor_icon("clock", size=13)}<span>{latency}</span></span>'
        f'<span>{phosphor_icon("coins", size=13)}<span>{tokens}</span></span>'
        f'<span>{phosphor_icon("currency-dollar", size=13)}<span>{cost}</span></span>'
        "</div>"
    )


def _format_latency(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return "—"


def _format_tokens(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _format_cost(value) -> str:
    if value is None:
        return "—"
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def _log_rag_context_to_browser(complaint_id: str, entry: dict):
    state = entry.get("state", {})
    raw = entry.get("input", {})
    latest_reflection = None
    reflection_logs = [item for item in entry.get("agent_log", []) if item.get("node") == "reflection_agent"]
    if reflection_logs:
        latest_reflection = reflection_logs[-1].get("output")
    rag_debug_payload = {"rag_query": state.get("rag_query", ""), "rag_results": state.get("rag_results", []) or []}
    if not rag_debug_payload["rag_results"] and state.get("valid_issue") and state.get("valid_sub_issue") and raw.get("narrative"):
        try:
            rag_debug_payload = get_rag_debug_payload(
                {
                    "valid_issue": state.get("valid_issue"),
                    "valid_sub_issue": state.get("valid_sub_issue"),
                    "valid_product": state.get("valid_product") or raw.get("product"),
                    "valid_sub_product": state.get("valid_sub_product") or raw.get("sub_product"),
                    "narrative": raw.get("narrative"),
                    "rag_query": state.get("rag_query"),
                    "rag_results": state.get("rag_results"),
                }
            )
        except Exception as exc:
            rag_debug_payload = {"rag_query": "", "rag_results": [{"error": str(exc)}]}
    payload = {
        "complaint_id": complaint_id,
        "status": entry.get("status"),
        "product": state.get("valid_product") or raw.get("product"),
        "sub_product": state.get("valid_sub_product") or raw.get("sub_product"),
        "issue": state.get("valid_issue") or raw.get("issue"),
        "sub_issue": state.get("valid_sub_issue") or raw.get("sub_issue"),
        "narrative": raw.get("narrative"),
        "applicable_regulation": state.get("applicable_regulation"),
        "citation": state.get("citation"),
        "compliance_explanation": state.get("compliance_explanation"),
        "rag_query": rag_debug_payload.get("rag_query", ""),
        "rag_results": rag_debug_payload.get("rag_results", []),
        "final_email": state.get("customer_email"),
        "reflection_agent": latest_reflection
        or {
            "reflection_passed": state.get("reflection_passed"),
            "reflection_feedback": state.get("reflection_feedback"),
            "reflection_score": state.get("reflection_score"),
            "reflection_attempts": state.get("reflection_attempts"),
        },
    }
    serialized = json.dumps(payload).replace("</", "<\\/")
    components.html(
        f"""
        <script>
          try {{
            parent.console.log("Complaint debug payload", {serialized});
          }} catch (err) {{
            console.log("Complaint debug payload", {serialized});
          }}
        </script>
        """,
        height=0,
    )


def _render_outputs(entry: dict):
    state = entry.get("state", {})
    raw = entry.get("input", {})
    if entry["status"] not in {"complete", "needs_review", "processing"}:
        if entry.get("error"):
            st.error(entry["error"])
        if entry.get("error_traceback"):
            with st.expander("Error Traceback"):
                st.code(entry["error_traceback"])
        with st.expander("Debug Events"):
            events = get_debug_events_for_complaint(entry["input"]["complaint_id"])
            if not events:
                st.caption("No debug events recorded yet.")
            else:
                st.json(events)
        return

    summary_html = "".join(
        [
            '<div class="summary-panel">',
            _summary_section_dropdown(
                "Original Complaint",
                [
                    ("Product", raw.get("product")),
                    ("Sub-Product", raw.get("sub_product")),
                    ("Issue", raw.get("issue")),
                    ("Sub-Issue", raw.get("sub_issue")),
                ],
                extra_body=_summary_subsection("Narrative", raw.get("narrative") or "—", preserve_lines=True),
            ),
            _summary_section_dropdown(
                "Classification",
                [
                    ("Product", state.get("valid_product")),
                    ("Sub-Product", state.get("valid_sub_product")),
                    ("Issue", state.get("valid_issue")),
                    ("Sub-Issue", state.get("valid_sub_issue")),
                    ("Confidence", f"{state['confidence']:.0%}" if "confidence" in state else "—"),
                ],
            ),
            _summary_section_dropdown(
                "Severity",
                [("Score", state.get("severity"))],
                callout=state.get("severity_explanation"),
            ),
            _summary_section_dropdown(
                "Compliance",
                [
                    ("Score", state.get("compliance")),
                    ("Regulation", state.get("applicable_regulation")),
                    ("Citation", state.get("citation")),
                ],
                callout=state.get("compliance_explanation"),
            ),
            _summary_section_dropdown(
                "Routing",
                [
                    ("Team", state.get("team")),
                    ("Priority", state.get("priority")),
                    ("SLA Deadline", state.get("sla_deadline")),
                ],
                callout=state.get("team_explanation"),
            ),
            _summary_details("Resolution Plan", state.get("remediation_steps") or "—"),
            _summary_details("Preventative Recommendations", state.get("preventative_recommendations") or "—"),
            _summary_details("Customer Email Draft", state.get("customer_email") or "—", preserve_lines=True),
            "</div>",
        ]
    )
    st.markdown(summary_html, unsafe_allow_html=True)
    if "reflection_score" in state:
        st.write(f"Reflection Score: {state['reflection_score']}/5")
    if entry.get("error_traceback"):
        with st.expander("Error Traceback", expanded=True):
            st.code(entry["error_traceback"])
    with st.expander("Debug Events"):
        events = get_debug_events_for_complaint(entry["input"]["complaint_id"])
        if not events:
            st.caption("No debug events recorded yet.")
        else:
            st.json(events)


def _summary_section_dropdown(
    title: str,
    rows: list[tuple[str, object]],
    *,
    callout: str | None = None,
    extra_body: str = "",
) -> str:
    items = []
    for label, value in rows:
        rendered = _summary_escape(value if value not in {None, ""} else "—")
        items.append(
            f'<div class="summary-row"><span class="summary-label">{html.escape(label)}:</span> '
            f'<span class="summary-value">{rendered}</span></div>'
        )
    callout_html = ""
    if callout:
        callout_html = f'<div class="summary-callout">{_summary_escape(callout, preserve_lines=True)}</div>'
    caret = phosphor_icon("caret-right", size=14)
    return (
        '<details class="summary-details summary-section" open>'
        f'<summary><span class="summary-details-title">{html.escape(title)}'
        f'<span class="summary-details-caret">{caret}</span></span></summary>'
        f'<div class="summary-details-body">{"".join(items)}{extra_body}{callout_html}</div>'
        "</details>"
    )


def _summary_details(title: str, body: object, *, preserve_lines: bool = False) -> str:
    caret = phosphor_icon("caret-right", size=14)
    return (
        '<details class="summary-details" open>'
        f'<summary><span class="summary-details-title">{html.escape(title)}'
        f'<span class="summary-details-caret">{caret}</span></span></summary>'
        f'<div class="summary-details-body">{_summary_escape(body, preserve_lines=preserve_lines)}</div>'
        "</details>"
    )


def _summary_subsection(title: str, body: object, *, preserve_lines: bool = False) -> str:
    return (
        f'<div class="summary-subsection"><div class="summary-subsection-title">{html.escape(title)}</div>'
        f'<div class="summary-subsection-body">{_summary_escape(body, preserve_lines=preserve_lines)}</div></div>'
    )


def _summary_escape(value: object, *, preserve_lines: bool = False) -> str:
    text = "—" if value in {None, ""} else str(value)
    escaped = html.escape(text)
    if preserve_lines:
        return escaped.replace("\n", "<br>")
    return escaped


def _inject_icon_styles():
    st.markdown(
        """
        <style>
        .agent-step-title {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            font-weight: 600;
            margin-bottom: 0.35rem;
            min-height: 2rem;
        }
        .agent-step-title .ph-icon {
            flex: 0 0 auto;
        }
        .agent-step-title.in-progress {
            color: rgba(250, 250, 250, 0.96);
        }
        .agent-shimmer-text {
            display: inline-block;
            background: linear-gradient(
                90deg,
                rgba(255,255,255,0.45) 0%,
                rgba(255,255,255,0.70) 32%,
                rgba(255,255,255,0.98) 50%,
                rgba(255,255,255,0.70) 68%,
                rgba(255,255,255,0.45) 100%
            );
            background-size: 200% auto;
            color: transparent;
            background-clip: text;
            -webkit-background-clip: text;
            animation: agent-shimmer 1.7s linear infinite;
        }
        .agent-spinner {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            animation: agent-spin 0.85s linear infinite;
            transform-origin: center;
        }
        .agent-connector {
            width: 2px;
            height: 18px;
            margin: 0.2rem 0 0.8rem 0.45rem;
            background: linear-gradient(to bottom, rgba(250,250,250,0.22), rgba(250,250,250,0.04));
        }
        .agent-parallel-gap {
            height: 0.45rem;
        }
        .agent-completed-step {
            margin: 0 0 0.35rem 0;
        }
        .agent-completed-step summary {
            list-style: none;
            display: inline-flex;
            align-items: center;
            cursor: pointer;
            padding: 0.1rem 0;
        }
        .agent-completed-step summary::-webkit-details-marker {
            display: none;
        }
        .agent-completed-title {
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
            font-weight: 600;
        }
        .agent-step-metric {
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            margin-left: 0.5rem;
            color: rgba(250,250,250,0.7);
            font-size: 0.8rem;
            font-weight: 500;
        }
        .agent-completed-caret {
            display: inline-flex;
            align-items: center;
            color: rgba(250,250,250,0.75);
            transition: transform 0.16s ease;
            margin-left: 0.3rem;
        }
        .agent-completed-step[open] .agent-completed-caret {
            transform: rotate(90deg);
        }
        .agent-completed-body {
            margin: 0.35rem 0 0.8rem 1.55rem;
            color: rgba(250,250,250,0.9);
        }
        .agent-output-line {
            margin-bottom: 0.35rem;
            line-height: 1.5;
        }
        .agent-output-label {
            font-weight: 600;
        }
        .agent-output-empty {
            color: rgba(250,250,250,0.7);
        }
        .summary-panel {
            background: var(--secondary-background-color, rgba(255,255,255,0.04));
            border-radius: 20px;
            padding: 1.1rem 1.1rem 0.5rem 1.1rem;
            border: none;
        }
        .complaint-metrics-caption {
            display: inline-flex;
            align-items: center;
            gap: 1rem;
            margin: -0.15rem 0 0.3rem 0;
            color: rgba(250,250,250,0.72);
            font-size: 0.88rem;
        }
        .complaint-metrics-caption span {
            display: inline-flex;
            align-items: center;
            gap: 0.28rem;
        }
        .summary-row {
            margin-bottom: 0.8rem;
            line-height: 1.5;
        }
        .summary-label {
            font-weight: 600;
        }
        .summary-callout {
            margin-top: 0.9rem;
            padding: 0.95rem 1rem;
            border-radius: 16px;
            background: rgba(59, 130, 246, 0.14);
            color: rgb(96, 165, 250);
            line-height: 1.65;
        }
        .summary-details {
            margin: 0 0 1rem 0;
        }
        .summary-details summary {
            list-style: none;
            display: inline-flex;
            align-items: center;
            cursor: pointer;
            font-weight: 600;
            margin-bottom: 0.7rem;
        }
        .summary-details summary::-webkit-details-marker {
            display: none;
        }
        .summary-details-title {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
        }
        .summary-details-caret {
            display: inline-flex;
            align-items: center;
            color: rgba(250,250,250,0.75);
            transition: transform 0.16s ease;
            margin-left: 0.05rem;
        }
        .summary-details[open] .summary-details-caret {
            transform: rotate(90deg);
        }
        .summary-details-body {
            line-height: 1.65;
            padding: 0 0 0.1rem 1.35rem;
        }
        .summary-subsection {
            margin-top: 0.9rem;
        }
        .summary-subsection-title {
            font-weight: 600;
            margin-bottom: 0.45rem;
        }
        .summary-subsection-body {
            line-height: 1.65;
        }
        @keyframes agent-spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        @keyframes agent-shimmer {
            0% { background-position: 200% center; }
            100% { background-position: -200% center; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
