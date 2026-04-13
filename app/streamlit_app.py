from __future__ import annotations

import csv
from io import StringIO

import streamlit as st

from app.components.add_complaint_modal import render_add_form
from app.components.agent_progress import render_detail_view
from app.components.complaint_table import render_table
from app.state_store import init_store, list_complaints, restart_complaint, resume_pipeline


def main():
    st.set_page_config(
        page_title="Resolv — Complaint Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    init_store()

    if "show_add_form" not in st.session_state:
        st.session_state.show_add_form = False
    if "selected_complaint" not in st.session_state:
        st.session_state.selected_complaint = None

    _handle_table_actions()

    complaints = list_complaints()
    title_col, button_col = st.columns([8, 2])
    with title_col:
        st.title("Complaint Dashboard")
        total = len(complaints)
        processing = sum(1 for item in complaints.values() if item["status"] == "processing")
        st.caption(f"{total} complaints total · {processing} currently processing")
    with button_col:
        st.write("")
        if st.button("+ Add Complaint", type="primary", use_container_width=True):
            st.session_state.show_add_form = True
            st.session_state.selected_complaint = None
        csv_data = _build_completed_complaints_csv(complaints)
        if csv_data is not None:
            st.download_button(
                "Download Completed CSV",
                data=csv_data,
                file_name="completed_complaints.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if st.session_state.show_add_form:
        render_add_form()

    _render_dashboard_metrics(complaints)

    if st.session_state.selected_complaint:
        render_detail_view(st.session_state.selected_complaint)
    elif not complaints:
        st.info("No complaints yet. Click '+ Add Complaint' to get started.")
    else:
        render_table()


def _handle_table_actions():
    action = st.query_params.get("action")
    complaint_id = st.query_params.get("complaint_id")
    if not action or not complaint_id:
        return

    complaint_id = str(complaint_id)
    if action == "view":
        st.session_state.selected_complaint = complaint_id
    elif action == "edit":
        st.session_state.selected_complaint = complaint_id
        st.session_state[f"editing_detail_{complaint_id}"] = True
    elif action == "approve":
        resume_pipeline(complaint_id)
    elif action == "restart":
        restart_complaint(complaint_id)
        st.session_state.selected_complaint = complaint_id

    st.query_params.clear()
    st.rerun()


def _render_dashboard_metrics(complaints: dict[str, dict]) -> None:
    _inject_metric_card_styles()
    avg_latency = _average_metric(complaints.values(), "total_latency_seconds")
    avg_tokens = _average_metric(complaints.values(), "total_tokens")
    avg_cost = _average_metric(complaints.values(), "total_cost")

    metric_columns = st.columns(3)
    cards = [
        ("Average Latency", _format_latency(avg_latency)),
        ("Average Tokens", _format_tokens(avg_tokens)),
        ("Average Cost", _format_cost(avg_cost)),
    ]
    for column, (label, value) in zip(metric_columns, cards):
        with column:
            st.markdown(
                f"""
                <div class="dashboard-metric-card">
                  <div class="dashboard-metric-label">{label}</div>
                  <div class="dashboard-metric-value">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _average_metric(entries, key: str) -> float | None:
    values = [entry.get(key) for entry in entries if isinstance(entry.get(key), (int, float))]
    if not values:
        return None
    return float(sum(values)) / len(values)


def _format_latency(value: float | None) -> str:
    return f"{value:.2f}s" if value is not None else "—"


def _format_tokens(value: float | None) -> str:
    return f"{int(round(value)):,}" if value is not None else "—"


def _format_cost(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "—"


def _inject_metric_card_styles() -> None:
    st.markdown(
        """
        <style>
        .dashboard-metric-card {
            border-radius: 18px;
            padding: 1rem 1.1rem;
            background: rgba(255, 255, 255, 0.035);
            border: 1px solid rgba(250, 250, 250, 0.08);
            margin: 0.25rem 0 1.25rem 0;
        }
        .dashboard-metric-label {
            color: rgba(250, 250, 250, 0.68);
            font-size: 0.92rem;
            font-weight: 600;
            margin-bottom: 0.35rem;
        }
        .dashboard-metric-value {
            color: rgba(250, 250, 250, 0.98);
            font-size: 1.7rem;
            font-weight: 700;
            line-height: 1.1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _build_completed_complaints_csv(complaints: dict[str, dict]) -> str | None:
    completed_rows: list[dict[str, object]] = []
    for complaint_id, entry in complaints.items():
        if entry.get("status") != "complete":
            continue
        state = entry.get("state", {})
        raw = entry.get("input", {})
        completed_rows.append(
            {
                "complaint_id": complaint_id,
                "status": entry.get("status"),
                "input_product": raw.get("product"),
                "input_sub_product": raw.get("sub_product"),
                "input_issue": raw.get("issue"),
                "input_sub_issue": raw.get("sub_issue"),
                "narrative": raw.get("narrative"),
                "valid_product": state.get("valid_product"),
                "valid_sub_product": state.get("valid_sub_product"),
                "valid_issue": state.get("valid_issue"),
                "valid_sub_issue": state.get("valid_sub_issue"),
                "confidence": state.get("confidence"),
                "root_cause": state.get("root_cause"),
                "severity": state.get("severity"),
                "severity_explanation": state.get("severity_explanation"),
                "compliance": state.get("compliance"),
                "compliance_explanation": state.get("compliance_explanation"),
                "applicable_regulation": state.get("applicable_regulation"),
                "citation": state.get("citation"),
                "team": state.get("team"),
                "team_explanation": state.get("team_explanation"),
                "priority": state.get("priority"),
                "sla_days": state.get("sla_days"),
                "sla_deadline": state.get("sla_deadline"),
                "needs_human_review": state.get("needs_human_review"),
                "review_reasons": "; ".join(state.get("review_reasons", []) or []),
                "remediation_steps": state.get("remediation_steps"),
                "preventative_recommendations": state.get("preventative_recommendations"),
                "customer_email": state.get("customer_email"),
                "reflection_score": state.get("reflection_score"),
                "reflection_passed": state.get("reflection_passed"),
                "reflection_attempts": state.get("reflection_attempts"),
                "total_latency_seconds": entry.get("total_latency_seconds"),
                "total_tokens": entry.get("total_tokens"),
                "total_cost": entry.get("total_cost"),
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
            }
        )
    if not completed_rows:
        return None

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=list(completed_rows[0].keys()))
    writer.writeheader()
    writer.writerows(completed_rows)
    return output.getvalue()


if __name__ == "__main__":
    main()
