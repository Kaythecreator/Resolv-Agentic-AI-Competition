from __future__ import annotations

import pandas as pd
import streamlit as st

from app.state_store import add_complaint, start_pipeline_thread
from app.taxonomy_helpers import get_issues, get_products, get_sub_issues, get_sub_products


REQUIRED_COLUMNS = ["complaint_id", "product", "sub_product", "issue", "sub_issue", "narrative"]
HEADER_ALIASES = {
    "Complaint ID": "complaint_id",
    "Product": "product",
    "Sub-product": "sub_product",
    "Issue": "issue",
    "Sub-issue": "sub_issue",
    "Consumer complaint narrative": "narrative",
}


def _normalize_row_keys(row: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in row.items():
        mapped_key = HEADER_ALIASES.get(str(key).strip(), str(key).strip())
        normalized[mapped_key] = value
    return normalized


@st.dialog("Add Complaint", width="large")
def render_add_form():
    manual_tab, upload_tab = st.tabs(["Manual Entry", "Upload CSV"])
    with manual_tab:
        _render_manual_form()
    with upload_tab:
        _render_csv_form()


def _render_manual_form():
    complaint_id = st.text_input("Complaint ID")
    product = st.selectbox("Product", [""] + get_products())

    sub_product = ""
    if product:
        sub_product = st.selectbox("Sub-Product", [""] + get_sub_products(product))

    issue = ""
    if sub_product:
        issue = st.selectbox("Issue", [""] + get_issues(product, sub_product))

    sub_issue = ""
    if issue:
        sub_issue = st.selectbox("Sub-Issue", [""] + get_sub_issues(product, sub_product, issue))

    narrative = st.text_area("Complaint Narrative", height=200)

    submit_col, cancel_col = st.columns(2)
    with submit_col:
        if st.button("Submit & Run", type="primary", use_container_width=True):
            if not complaint_id.strip() or not narrative.strip():
                st.error("Complaint ID and narrative are required.")
                return
            input_data = {
                "complaint_id": complaint_id,
                "product": product,
                "sub_product": sub_product,
                "issue": issue,
                "sub_issue": sub_issue,
                "narrative": narrative,
            }
            if not add_complaint(complaint_id.strip(), input_data):
                st.warning("Complaint ID already exists.")
                return
            st.session_state.show_add_form = False
            st.session_state.selected_complaint = complaint_id.strip()
            start_pipeline_thread(complaint_id.strip())
            st.rerun()

    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.session_state.show_add_form = False
            st.rerun()


def _render_csv_form():
    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded_file is None:
        return

    try:
        frame = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Unable to read CSV: {exc}")
        return

    frame = frame.rename(columns=lambda column: HEADER_ALIASES.get(str(column).strip(), str(column).strip()))

    missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
    if missing:
        st.error(f"Missing required columns: {', '.join(missing)}")
        return

    skipped: list[str] = []
    queued: list[str] = []
    for row in frame.to_dict(orient="records"):
        row = _normalize_row_keys(row)
        complaint_id = str(row.get("complaint_id", "")).strip()
        narrative = str(row.get("narrative", "") or "").strip()
        if not complaint_id or not narrative:
            skipped.append(complaint_id or "<missing id>")
            continue
        if len(narrative) > 5000:
            row["narrative"] = narrative[:5000]
        if add_complaint(complaint_id, row):
            queued.append(complaint_id)
            start_pipeline_thread(complaint_id)
        else:
            skipped.append(complaint_id)

    if queued:
        st.session_state.show_add_form = False
        st.session_state.selected_complaint = queued[0]
        st.rerun()
    if skipped:
        st.warning(f"Skipped: {', '.join(skipped)}")
