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
        initial_sidebar_state="expanded",
    )

    init_store()

    if "show_add_form" not in st.session_state:
        st.session_state.show_add_form = False
    if "selected_complaint" not in st.session_state:
        st.session_state.selected_complaint = None

    _inject_metric_card_styles()
    _handle_table_actions()

    complaints = list_complaints()
    current_view = _render_example_sidebar()
    if current_view == "Evaluation Metrics":
        _render_metrics_example_page()
        return

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


def _render_example_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            """
            <div class="resolv-sidebar-brand">
              <div class="resolv-sidebar-logo">
                <div class="resolv-sidebar-logo-mark">R</div>
                <div>
                  <div class="resolv-sidebar-logo-name">Resolv</div>
                  <div class="resolv-sidebar-logo-subtitle">Agentic complaint operations</div>
                </div>
              </div>
              <div class="resolv-sidebar-tag">UMD Agentic AI Challenge 2026</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if "sidebar_workspace_view" not in st.session_state:
            st.session_state.sidebar_workspace_view = "Complaint Dashboard"

        st.markdown('<div class="resolv-sidebar-section-label">Workspace</div>', unsafe_allow_html=True)

        current_view = st.session_state.sidebar_workspace_view
        if st.button(
            "Complaint Dashboard",
            key="sidebar_dashboard_button",
            icon=":material/dashboard:",
            type="primary" if current_view == "Complaint Dashboard" else "secondary",
            use_container_width=True,
        ):
            st.session_state.sidebar_workspace_view = "Complaint Dashboard"
            current_view = "Complaint Dashboard"

        if st.button(
            "Evaluation Metrics",
            key="sidebar_metrics_button",
            icon=":material/monitoring:",
            type="primary" if current_view == "Evaluation Metrics" else "secondary",
            use_container_width=True,
        ):
            st.session_state.sidebar_workspace_view = "Evaluation Metrics"
            current_view = "Evaluation Metrics"

    return current_view


def _render_metrics_example_page() -> None:
    st.title("Evaluation Metrics")
    st.caption("Example presentation page accessible from the sidebar. This view is not connected to backend metrics yet.")
    st.markdown(
        """
        <style>
        .metrics-example-shell {
            position: relative;
            overflow: hidden;
            border-radius: 24px;
            padding: 1.8rem;
            background:
                radial-gradient(circle at top left, rgba(37,99,235,0.12), transparent 28%),
                radial-gradient(circle at bottom right, rgba(139,92,246,0.12), transparent 26%),
                #0a0e1a;
            border: 1px solid rgba(30, 45, 69, 0.8);
            margin-top: 0.6rem;
        }
        .metrics-example-shell::before {
            content: "";
            position: absolute;
            inset: 0;
            background-image:
                linear-gradient(rgba(37,99,235,0.035) 1px, transparent 1px),
                linear-gradient(90deg, rgba(37,99,235,0.035) 1px, transparent 1px);
            background-size: 48px 48px;
            pointer-events: none;
        }
        .metrics-example-inner { position: relative; z-index: 1; }
        .metrics-example-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 1.8rem;
        }
        .metrics-example-logo { display: flex; align-items: center; gap: 0.8rem; }
        .metrics-example-mark {
            width: 2.25rem; height: 2.25rem; border-radius: 0.7rem; display: flex;
            align-items: center; justify-content: center; background: #2563eb; color: white; font-weight: 800;
        }
        .metrics-example-name { color: rgba(241,245,249,0.98); font-size: 1.28rem; font-weight: 800; }
        .metrics-example-tag {
            color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
            border: 1px solid rgba(30,45,69,0.9); background: rgba(15,23,42,0.9);
            border-radius: 999px; padding: 0.36rem 0.75rem;
        }
        .metrics-example-title { margin-bottom: 1.6rem; }
        .metrics-example-title h1 { color: #f1f5f9; font-size: 2rem; line-height: 1.08; margin: 0 0 0.3rem 0; }
        .metrics-example-title p { color: #64748b; font-size: 0.95rem; margin: 0; }
        .metrics-top-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.8rem; margin-bottom: 1rem; }
        .metrics-top-card {
            border-radius: 14px; padding: 0.95rem 1rem; background: #111827; border: 1px solid #1e2d45;
            position: relative; overflow: hidden;
        }
        .metrics-top-card::before { content: ""; position: absolute; left: 0; top: 0; right: 0; height: 2px; }
        .metrics-top-card.blue::before { background: #2563eb; }
        .metrics-top-card.green::before { background: #10b981; }
        .metrics-top-card.amber::before { background: #f59e0b; }
        .metrics-top-card.purple::before { background: #8b5cf6; }
        .metrics-top-label { color: #64748b; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 0.35rem; font-weight: 700; }
        .metrics-top-value { font-size: 1.4rem; font-weight: 800; line-height: 1.05; }
        .metrics-top-card.blue .metrics-top-value { color: #60a5fa; }
        .metrics-top-card.green .metrics-top-value { color: #34d399; }
        .metrics-top-card.amber .metrics-top-value { color: #fbbf24; }
        .metrics-top-card.purple .metrics-top-value { color: #a78bfa; }
        .metrics-top-sub { color: #64748b; font-size: 0.72rem; margin-top: 0.15rem; }
        .metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
        .metrics-card { border-radius: 16px; padding: 1.15rem 1.2rem; background: #111827; border: 1px solid #1e2d45; }
        .metrics-card-header { display: flex; align-items: center; justify-content: space-between; gap: 0.8rem; margin-bottom: 0.95rem; }
        .metrics-card-title { display: flex; align-items: center; gap: 0.6rem; color: #f1f5f9; font-size: 0.95rem; font-weight: 700; }
        .metrics-card-dot { width: 0.52rem; height: 0.52rem; border-radius: 999px; display: inline-block; }
        .metrics-card.blue .metrics-card-dot { background: #2563eb; box-shadow: 0 0 10px rgba(37,99,235,0.8); }
        .metrics-card.green .metrics-card-dot { background: #10b981; box-shadow: 0 0 10px rgba(16,185,129,0.8); }
        .metrics-card.amber .metrics-card-dot { background: #f59e0b; box-shadow: 0 0 10px rgba(245,158,11,0.8); }
        .metrics-card.purple .metrics-card-dot { background: #8b5cf6; box-shadow: 0 0 10px rgba(139,92,246,0.8); }
        .metrics-card-badge {
            color: #94a3b8; font-size: 0.68rem; padding: 0.22rem 0.5rem; border-radius: 999px;
            background: rgba(15,23,42,0.8); border: 1px solid rgba(30,45,69,0.9);
        }
        .metrics-row { display: grid; grid-template-columns: 96px 1fr 44px; gap: 0.7rem; align-items: center; margin-bottom: 0.55rem; }
        .metrics-row-label, .metrics-row-val { color: #94a3b8; font-size: 0.73rem; }
        .metrics-row-val { text-align: right; }
        .metrics-track { height: 6px; border-radius: 999px; background: #1e293b; overflow: hidden; }
        .metrics-fill { height: 100%; border-radius: 999px; }
        .metrics-card.blue .metrics-fill { background: #2563eb; }
        .metrics-card.green .metrics-fill { background: #10b981; }
        .metrics-card.amber .metrics-fill { background: #f59e0b; }
        .metrics-card.purple .metrics-fill { background: #8b5cf6; }
        .metrics-card-desc { color: #64748b; font-size: 0.76rem; line-height: 1.6; margin-top: 0.9rem; padding-top: 0.85rem; border-top: 1px solid #1e2d45; }
        .metrics-footer {
            display: flex; align-items: center; justify-content: space-between; gap: 1rem; border-radius: 14px;
            padding: 0.95rem 1rem; background: #111827; border: 1px solid #1e2d45; margin-top: 1rem;
            color: #94a3b8; font-size: 0.74rem; letter-spacing: 0.05em; text-transform: uppercase;
        }
        .metrics-footer-stats { display: flex; gap: 1.2rem; }
        .metrics-footer-stat strong {
            display: block; color: #f1f5f9; font-size: 0.95rem; letter-spacing: normal; text-transform: none;
        }
        @media (max-width: 1100px) {
            .metrics-top-grid, .metrics-grid { grid-template-columns: 1fr 1fr; }
        }
        @media (max-width: 760px) {
            .metrics-example-header, .metrics-footer { flex-direction: column; align-items: flex-start; }
            .metrics-top-grid, .metrics-grid { grid-template-columns: 1fr; }
        }
        </style>
        <div class="metrics-example-shell">
          <div class="metrics-example-inner">
            <div class="metrics-example-header">
              <div class="metrics-example-logo">
                <div class="metrics-example-mark">R</div>
                <div class="metrics-example-name">Resolv</div>
              </div>
              <div class="metrics-example-tag">UMD Agentic AI Challenge 2026</div>
            </div>
            <div class="metrics-example-title">
              <h1>Evaluation Metrics</h1>
              <p>Performance across accuracy, resolution quality, fairness, and customer satisfaction</p>
            </div>
            <div class="metrics-top-grid">
              <div class="metrics-top-card blue"><div class="metrics-top-label">Cost / Complaint</div><div class="metrics-top-value">$0.014</div><div class="metrics-top-sub">vs $8–12 manual</div></div>
              <div class="metrics-top-card green"><div class="metrics-top-label">Latency</div><div class="metrics-top-value">16.6s</div><div class="metrics-top-sub">vs 35 min manual</div></div>
              <div class="metrics-top-card amber"><div class="metrics-top-label">Complaints Run</div><div class="metrics-top-value">200</div><div class="metrics-top-sub">via LangSmith traces</div></div>
              <div class="metrics-top-card purple"><div class="metrics-top-label">Cost Reduction</div><div class="metrics-top-value">99.8%</div><div class="metrics-top-sub">vs manual processing</div></div>
            </div>
            <div class="metrics-grid">
              <div class="metrics-card blue">
                <div class="metrics-card-header"><div class="metrics-card-title"><span class="metrics-card-dot"></span><span>Accuracy</span></div><div class="metrics-card-badge">Expert-labeled GT</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Product</div><div class="metrics-track"><div class="metrics-fill" style="width:89%"></div></div><div class="metrics-row-val">89%</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Issue Type</div><div class="metrics-track"><div class="metrics-fill" style="width:82%"></div></div><div class="metrics-row-val">82%</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Team Routing</div><div class="metrics-track"><div class="metrics-fill" style="width:91%"></div></div><div class="metrics-row-val">91%</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Weighted F1</div><div class="metrics-track"><div class="metrics-fill" style="width:85%"></div></div><div class="metrics-row-val">0.85</div></div>
                <div class="metrics-card-desc">100–200 complaints manually labeled by a CFPB subject matter expert across severity, compliance risk, citation, priority, and team assignment.</div>
              </div>
              <div class="metrics-card green">
                <div class="metrics-card-header"><div class="metrics-card-title"><span class="metrics-card-dot"></span><span>Resolution Quality</span></div><div class="metrics-card-badge">LLM-as-Judge</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Specificity</div><div class="metrics-track"><div class="metrics-fill" style="width:86%"></div></div><div class="metrics-row-val">4.3/5</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Compliance</div><div class="metrics-track"><div class="metrics-fill" style="width:92%"></div></div><div class="metrics-row-val">4.6/5</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Tone</div><div class="metrics-track"><div class="metrics-fill" style="width:88%"></div></div><div class="metrics-row-val">4.4/5</div></div>
                <div class="metrics-row"><div class="metrics-row-label">No Liability</div><div class="metrics-track"><div class="metrics-fill" style="width:96%"></div></div><div class="metrics-row-val">4.8/5</div></div>
                <div class="metrics-card-desc">LangSmith LLM-as-judge scores each email on specificity, regulatory compliance, tone, and absence of liability admissions.</div>
              </div>
              <div class="metrics-card amber">
                <div class="metrics-card-header"><div class="metrics-card-title"><span class="metrics-card-dot"></span><span>Fairness</span></div><div class="metrics-card-badge">Demographic Parity</div></div>
                <div class="metrics-row"><div class="metrics-row-label">General</div><div class="metrics-track"><div class="metrics-fill" style="width:67%"></div></div><div class="metrics-row-val">10d SLA</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Older American</div><div class="metrics-track"><div class="metrics-fill" style="width:67%"></div></div><div class="metrics-row-val">10d SLA</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Servicemember</div><div class="metrics-track"><div class="metrics-fill" style="width:7%"></div></div><div class="metrics-row-val">1d SLA</div></div>
                <div class="metrics-row"><div class="metrics-row-label">SLA Variance</div><div class="metrics-track"><div class="metrics-fill" style="width:0%"></div></div><div class="metrics-row-val">0d ✓</div></div>
                <div class="metrics-card-desc">Results segmented by CFPB demographic tags. Servicemembers receive accelerated 1-day SLA per MLA requirements.</div>
              </div>
              <div class="metrics-card purple">
                <div class="metrics-card-header"><div class="metrics-card-title"><span class="metrics-card-dot"></span><span>Customer Satisfaction</span></div><div class="metrics-card-badge">Blind Survey</div></div>
                <div class="metrics-row"><div class="metrics-row-label">AI (Resolv)</div><div class="metrics-track"><div class="metrics-fill" style="width:84%"></div></div><div class="metrics-row-val">4.2/5</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Human Analyst</div><div class="metrics-track"><div class="metrics-fill" style="width:72%"></div></div><div class="metrics-row-val">3.6/5</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Time (AI)</div><div class="metrics-track"><div class="metrics-fill" style="width:2%"></div></div><div class="metrics-row-val">16.6s</div></div>
                <div class="metrics-row"><div class="metrics-row-label">Time (Human)</div><div class="metrics-track"><div class="metrics-fill" style="width:100%"></div></div><div class="metrics-row-val">35 min</div></div>
                <div class="metrics-card-desc">Example evaluation page only. This view is not connected to backend metrics yet and is meant as a design sample.</div>
              </div>
            </div>
            <div class="metrics-footer">
              <div>Team Resolv · CFPB Complaint Categorization</div>
              <div class="metrics-footer-stats">
                <div class="metrics-footer-stat"><strong>8 Agents</strong>LangGraph</div>
                <div class="metrics-footer-stat"><strong>4,803</strong>RAG Chunks</div>
                <div class="metrics-footer-stat"><strong>7 Regs</strong>eCFR Indexed</div>
              </div>
            </div>
          </div>
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
        [data-testid="stSidebar"] > div:first-child {
            background:
                radial-gradient(circle at top left, rgba(37,99,235,0.16), transparent 30%),
                radial-gradient(circle at bottom right, rgba(139,92,246,0.14), transparent 28%),
                #0a0e1a;
        }
        .resolv-sidebar-brand {
            margin: 0.5rem 0 1.15rem 0;
        }
        .resolv-sidebar-logo {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            margin-bottom: 0.8rem;
        }
        .resolv-sidebar-logo-mark {
            width: 2.2rem;
            height: 2.2rem;
            border-radius: 0.7rem;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 1rem;
            color: white;
            background: linear-gradient(135deg, #2563eb 0%, #3b82f6 100%);
            box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.25);
        }
        .resolv-sidebar-logo-name {
            color: rgba(248, 250, 252, 0.98);
            font-size: 1.15rem;
            font-weight: 800;
            line-height: 1.05;
        }
        .resolv-sidebar-logo-subtitle {
            color: rgba(148, 163, 184, 0.82);
            font-size: 0.8rem;
            margin-top: 0.14rem;
        }
        .resolv-sidebar-tag {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 0.35rem 0.7rem;
            color: rgba(148, 163, 184, 0.95);
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid rgba(51, 65, 85, 0.7);
            font-size: 0.7rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .resolv-sidebar-section-label {
            color: rgba(148, 163, 184, 0.76);
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin: 1.25rem 0 0.55rem 0;
            font-weight: 700;
        }
        [data-testid="stSidebar"] .stButton {
            margin-bottom: 0.45rem;
        }
        [data-testid="stSidebar"] .stButton > button {
            min-height: 3rem;
            justify-content: flex-start;
            border-radius: 0.95rem;
            padding: 0.78rem 0.95rem;
            font-size: 0.94rem;
            font-weight: 650;
            border: 1px solid rgba(30, 41, 59, 0.9);
            background: rgba(15, 23, 42, 0.58);
            color: rgba(226, 232, 240, 0.94);
            box-shadow: none;
            transition: background 120ms ease, border-color 120ms ease, transform 120ms ease;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            border-color: rgba(59, 130, 246, 0.42);
            background: rgba(17, 24, 39, 0.88);
            transform: translateY(-1px);
        }
        [data-testid="stSidebar"] .stButton > button[kind="primary"] {
            background: rgba(17, 24, 39, 0.96);
            border-color: rgba(59, 130, 246, 0.48);
            box-shadow: inset 0 1px 0 rgba(96, 165, 250, 0.12);
        }
        [data-testid="stSidebar"] .stButton > button p {
            font-size: 0.94rem;
            font-weight: 650;
        }
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
