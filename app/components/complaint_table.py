from __future__ import annotations

from html import escape
from urllib.parse import urlencode

import streamlit as st

from app.state_store import list_complaints
from app.ui.icons import phosphor_icon


STATUS_COLORS = {
    "pending": "#757575",
    "processing": "#1E88E5",
    "needs_review": "#FB8C00",
    "complete": "#43A047",
    "error": "#E53935",
}

HEADERS = [
    ("Complaint ID", "7rem"),
    ("Status", "10rem"),
    ("Latency", "6.5rem"),
    ("Tokens", "7rem"),
    ("Cost", "6.5rem"),
    ("Product", "clamp(11rem, 14vw, 14rem)"),
    ("Sub-Product", "clamp(10rem, 12vw, 13rem)"),
    ("Issue", "clamp(10rem, 12vw, 13rem)"),
    ("Sub-Issue", "clamp(11rem, 14vw, 15rem)"),
    ("Confidence", "6.5rem"),
    ("Severity", "6rem"),
    ("Compliance", "7rem"),
    ("Regulation", "7rem"),
    ("Citation", "clamp(8rem, 10vw, 11rem)"),
    ("Team", "clamp(8rem, 10vw, 11rem)"),
    ("Priority", "6rem"),
    ("SLA", "6rem"),
    ("Review?", "6rem"),
    ("Actions", "8rem"),
]


def render_table():
    complaints = list_complaints()
    _inject_table_styles()

    rows = []
    for complaint_id, data in complaints.items():
        state = data.get("state", {})
        raw = data.get("input", {})
        review_value = "Yes" if state.get("needs_human_review") else "No" if "needs_human_review" in state else "—"
        values = [
            complaint_id,
            _badge_html(data["status"]),
            _format_latency(data.get("total_latency_seconds")),
            _format_tokens(data.get("total_tokens")),
            _format_cost(data.get("total_cost")),
            state.get("valid_product") or raw.get("product") or "—",
            state.get("valid_sub_product") or raw.get("sub_product") or "—",
            state.get("valid_issue") or raw.get("issue") or "—",
            state.get("valid_sub_issue") or raw.get("sub_issue") or "—",
            _format_percent(state.get("confidence")),
            _format_score(state.get("severity")),
            _format_score(state.get("compliance")),
            state.get("applicable_regulation") or "—",
            state.get("citation") or "—",
            state.get("team") or "—",
            state.get("priority") or "—",
            _format_days(state.get("sla_days")),
            review_value,
            _actions_html(complaint_id, data["status"], data.get("can_resume_review", False), data.get("can_restart", False)),
        ]
        cells = []
        for idx, value in enumerate(values):
            if idx == 1:
                cells.append(f'<td class="status-cell">{value}</td>')
            elif idx == len(values) - 1:
                cells.append(f'<td class="actions-cell">{value}</td>')
            else:
                cells.append(_td(str(value)))
        rows.append("<tr>" + "".join(cells) + "</tr>")

    header_cells = "".join(f"<th>{escape(label)}</th>" for label, _ in HEADERS)
    colgroup = "".join(f'<col style="width:{width};">' for _, width in HEADERS)
    table_html = f"""
    <div class="complaint-table-wrap">
      <table class="complaint-table">
        <colgroup>{colgroup}</colgroup>
        <thead><tr>{header_cells}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)


def _inject_table_styles():
    st.markdown(
        """
        <style>
        .complaint-table-wrap {
            width: 100%;
            overflow-x: auto;
            overflow-y: hidden;
            border: 1px solid rgba(250, 250, 250, 0.08);
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.02);
        }
        .complaint-table {
            width: max(100%, 110rem);
            border-collapse: collapse;
            table-layout: fixed;
        }
        .complaint-table thead th {
            text-align: left;
            font-weight: 600;
            color: rgba(250, 250, 250, 0.96);
            padding: 1rem 1rem;
            border-bottom: 1px solid rgba(250, 250, 250, 0.08);
            white-space: nowrap;
            position: sticky;
            top: 0;
            background: #0f1117;
            z-index: 1;
        }
        .complaint-table tbody td {
            padding: 1rem 1rem;
            border-bottom: 1px solid rgba(250, 250, 250, 0.08);
            vertical-align: top;
            color: rgba(250, 250, 250, 0.94);
        }
        .complaint-table tbody tr:hover {
            background: rgba(255, 255, 255, 0.025);
        }
        .complaint-table .cell {
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .complaint-table .cell.multiline {
            white-space: normal;
            overflow-wrap: anywhere;
            line-height: 1.45;
        }
        .complaint-table .status-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0.32rem 0.72rem;
            border-radius: 999px;
            color: white;
            font-size: 0.92rem;
            font-weight: 600;
            line-height: 1.2;
            white-space: nowrap;
            min-width: 7.75rem;
        }
        .complaint-table .actions {
            display: flex;
            justify-content: flex-end;
            gap: 0.45rem;
        }
        .complaint-table .action-link {
            width: 2.3rem;
            height: 2.3rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 12px;
            border: 1px solid rgba(250, 250, 250, 0.14);
            background: rgba(255, 255, 255, 0.03);
            text-decoration: none;
            font-size: 1rem;
            color: rgba(250, 250, 250, 0.94);
        }
        .complaint-table .action-link:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(250, 250, 250, 0.24);
        }
        .complaint-table .ph-icon {
            display: block;
        }
        @media (max-width: 1100px) {
            .complaint-table {
                width: 110rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _td(value: str) -> str:
    multiline = len(value) > 28 or " " in value and len(value.split()) > 3
    klass = "cell multiline" if multiline else "cell"
    safe_value = escape(value)
    return f'<td><div class="{klass}" title="{safe_value}">{safe_value}</div></td>'


def _badge_html(status: str) -> str:
    color = STATUS_COLORS.get(status, "#757575")
    safe_status = escape(status.replace("_", " "))
    return f'<span class="status-pill" style="background:{color};">{safe_status}</span>'


def _actions_html(complaint_id: str, status: str, can_resume_review: bool, can_restart: bool) -> str:
    actions: list[str] = []
    if status == "needs_review" and can_resume_review:
        actions.append(_action_link("approve", complaint_id, phosphor_icon("check-circle"), "Approve"))
        actions.append(_action_link("edit", complaint_id, phosphor_icon("pencil-simple"), "Edit"))
    elif can_restart:
        actions.append(
            _action_link(
                "restart",
                complaint_id,
                phosphor_icon("arrow-counter-clockwise"),
                "Restart run",
            )
        )
    actions.append(_action_link("view", complaint_id, phosphor_icon("eye"), "View"))
    return f'<div class="actions">{"".join(actions)}</div>'


def _action_link(action: str, complaint_id: str, icon_html: str, label: str) -> str:
    query = urlencode({"action": action, "complaint_id": complaint_id})
    return f'<a class="action-link" href="?{query}" title="{escape(label)}" aria-label="{escape(label)}">{icon_html}</a>'


def _format_percent(value):
    return f"{value:.0%}" if isinstance(value, (float, int)) else "—"


def _format_score(value):
    return f"{int(value)}/10" if isinstance(value, (float, int)) else "—"


def _format_days(value):
    return f"{int(value)} days" if isinstance(value, (float, int)) else "—"


def _format_latency(value):
    return f"{float(value):.2f}s" if isinstance(value, (float, int)) else "—"


def _format_tokens(value):
    return f"{int(value):,}" if isinstance(value, (float, int)) else "—"


def _format_cost(value):
    return f"${float(value):.4f}" if isinstance(value, (float, int)) else "—"
