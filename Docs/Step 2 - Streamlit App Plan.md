# Step 2 — Streamlit App Implementation Plan

This document is a complete, step-by-step guide for building the Streamlit front-end
that wraps the multi-agent complaint processing pipeline from `Test.ipynb`.
Follow every section in order. Do not skip steps.

---

## Overview of What You Are Building

A Streamlit web app with two views:

1. **Dashboard View** — a table of all complaints with key metadata (no long explanation
   text). Has an "Add Complaint" button in the top-right corner.
2. **Complaint Detail View** — opened by clicking any row in the table. Shows the
   real-time agent processing stream for that complaint plus the final output once done.

---

## File Structure to Create

```
/app
  streamlit_app.py        ← Main entry point
  agent_pipeline.py       ← All agent functions extracted from the notebook (pure Python)
  state_store.py          ← In-memory store for complaint state across reruns
  taxonomy_helpers.py     ← Helpers to load taxonomy.json and drive dropdowns
  components/
    complaint_table.py    ← Renders the main dashboard table
    add_complaint_modal.py ← The "Add Complaint" sidebar/form
    agent_progress.py     ← Real-time agent progress panel
```

> **Note:** Extract all agent code from `Test.ipynb` into `agent_pipeline.py` first.
> The notebook is the source of truth. Do not rewrite logic — just move it into `.py` files.

---

## Step 1 — Extract the Agent Pipeline into `agent_pipeline.py`

Take every code cell from `Test.ipynb` and consolidate them into a single importable
Python file: `agent_pipeline.py`.

### What goes in `agent_pipeline.py`:

1. **All imports** (copy from Cell 1)
2. **LLM initialization** — load from environment variable, do NOT hardcode the key
3. **Taxonomy loader** — `with open("taxonomy.json") as f: taxonomy = json.load(f)`
4. **ChromaDB loader** — load the already-built vector store from `./regulation_index`
   ```python
   embeddings = OpenAIEmbeddings()
   regulation_store = Chroma(
       collection_name="cfpb_regulations",
       embedding_function=embeddings,
       persist_directory="./regulation_index"
   )
   ```
   Do NOT rebuild the index on startup. It is already built and saved.
5. **All Pydantic output models** (from Cell 4):
   `ProductOutput`, `SubProductOutput`, `IssueOutput`, `SubIssueOutput`,
   `RootCauseOutput`, `SeverityOutput`, `ComplianceOutput`, `ReflectionOutput`,
   `TeamOutput`, `ResolutionOutput`, `CustomerEmailOutput`
6. **All agent node functions** (from Cell 11):
   `validate_issue`, `root_cause_analysis`, `severity_assessment`,
   `compliance_assessment`, `aggregate_results`, `assign_role`,
   `review_router`, `route_decision`, `human_input`, `create_resolution`,
   `create_customer_email`, `reflection_agent`
7. **Routing helpers** (from Cell 13): `route_reflection`
8. **Graph builder function** — wrap the graph construction (Cell 14) in a function
   called `build_graph()` that returns a freshly compiled `StateGraph`. This allows
   each complaint to get its own graph instance with its own `MemorySaver`.
   ```python
   def build_graph():
       memory = MemorySaver()
       parallel = StateGraph(State)
       # ... all add_node and add_edge calls ...
       return parallel.compile(checkpointer=memory, interrupt_before=["human_input"])
   ```

---

## Step 2 — Build `state_store.py`

This module is the single source of truth for all complaint data across Streamlit reruns.
Streamlit reruns the entire script on every interaction, so state must be persisted
somewhere. Use a combination of `st.session_state` and a module-level dict.

```python
# state_store.py
import streamlit as st
from typing import Dict, Any

def init_store():
    """Call this at the top of every Streamlit page."""
    if "complaints" not in st.session_state:
        st.session_state.complaints = {}
        # Key = complaint_id (str)
        # Value = dict with these keys:
        #   "input"        : the raw input dict passed to the graph
        #   "status"       : one of "pending" | "processing" | "needs_review" | "complete"
        #   "state"        : the latest LangGraph State dict (updated as agents finish)
        #   "agent_log"    : list of dicts {"agent": str, "status": "running"|"complete", "output": dict}
        #   "graph"        : the compiled LangGraph instance for this complaint
        #   "thread_config": {"configurable": {"thread_id": complaint_id}}
        #   "error"        : str or None

def get_complaint(complaint_id: str) -> Dict[str, Any]:
    return st.session_state.complaints.get(complaint_id)

def add_complaint(complaint_id: str, input_data: dict):
    graph = build_graph()  # import from agent_pipeline
    st.session_state.complaints[complaint_id] = {
        "input": input_data,
        "status": "pending",
        "state": {},
        "agent_log": [],
        "graph": graph,
        "thread_config": {"configurable": {"thread_id": complaint_id}},
        "error": None
    }

def update_complaint(complaint_id: str, updates: dict):
    st.session_state.complaints[complaint_id].update(updates)
```

---

## Step 3 — Build `taxonomy_helpers.py`

This powers the cascading dropdowns in the Add Complaint form.

```python
# taxonomy_helpers.py
import json

with open("taxonomy.json") as f:
    TAXONOMY = json.load(f)

def get_products() -> list[str]:
    return list(TAXONOMY.keys())

def get_sub_products(product: str) -> list[str]:
    return list(TAXONOMY.get(product, {}).keys())

def get_issues(product: str, sub_product: str) -> list[str]:
    return list(TAXONOMY.get(product, {}).get(sub_product, {}).keys())

def get_sub_issues(product: str, sub_product: str, issue: str) -> list[str]:
    return TAXONOMY.get(product, {}).get(sub_product, {}).get(issue, [])
```

---

## Step 4 — Build `components/add_complaint_modal.py`

This renders the "Add Complaint" form inside a Streamlit sidebar.

### Trigger

In the main app, put a button in the top-right column:
```python
col1, col2 = st.columns([8, 2])
with col1:
    st.title("Complaint Dashboard")
with col2:
    if st.button("+ Add Complaint", type="primary"):
        st.session_state.show_add_form = True
```

When `st.session_state.show_add_form` is `True`, open a sidebar with the form.

### Form Layout

The sidebar should have two tabs at the top:

**Tab 1: Manual Entry**

Render fields in this exact order. Each field depends on the one above it — use
`st.selectbox` with the taxonomy helpers to cascade options.

```
1. Complaint ID        → st.text_input("Complaint ID")
2. Product             → st.selectbox("Product", get_products())
3. Sub-Product         → st.selectbox("Sub-Product", get_sub_products(product))
4. Issue               → st.selectbox("Issue", get_issues(product, sub_product))
5. Sub-Issue           → st.selectbox("Sub-Issue", get_sub_issues(product, sub_product, issue))
6. Narrative           → st.text_area("Complaint Narrative", height=200)
```

Every dropdown below Product should only render AFTER the field above it has a value.
Use `if product:` guards before rendering each subsequent dropdown.

At the bottom of the form:
- A **"Submit & Run"** button
- A **"Cancel"** button that sets `st.session_state.show_add_form = False`

On Submit:
1. Validate that complaint_id and narrative are not empty. Show `st.error()` if they are.
2. Call `state_store.add_complaint(complaint_id, input_data)` where `input_data` is:
   ```python
   {
       "complaint_id": complaint_id,
       "product": product,
       "sub_product": sub_product,
       "issue": issue,
       "sub_issue": sub_issue,
       "narrative": narrative
   }
   ```
3. Close the form (`st.session_state.show_add_form = False`)
4. Start the background processing thread (see Step 6)
5. Set `st.session_state.selected_complaint = complaint_id` so the detail view opens automatically

**Tab 2: Upload CSV**

```python
uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
```

Expected CSV columns (map these headers exactly):
```
complaint_id, product, sub_product, issue, sub_issue, narrative
```

On upload:
1. Read with `pd.read_csv(uploaded_file)`
2. Validate required columns exist. Show `st.error()` if any are missing.
3. For each row, call `state_store.add_complaint(row["complaint_id"], row.to_dict())`
4. For each complaint, start a background processing thread (see Step 6)
5. Close the form
6. Show a `st.success(f"Queued {len(df)} complaints for processing")`

---

## Step 5 — Build `components/complaint_table.py`

This renders the main dashboard table.

### Columns to Show

Show these columns from the complaint state. If a value is not yet available
(still processing), show `"—"`.

| Column Header         | State Key               | Notes                            |
|-----------------------|-------------------------|----------------------------------|
| Complaint ID          | `complaint_id`          |                                  |
| Status                | `status`                | Badge: pending / processing / needs review / complete |
| Product               | `valid_product`         | Falls back to `product` if not yet classified |
| Sub-Product           | `valid_sub_product`     | Falls back to `sub_product`      |
| Issue                 | `valid_issue`           | Falls back to `issue`            |
| Sub-Issue             | `valid_sub_issue`       | Falls back to `sub_issue`        |
| Confidence            | `confidence`            | Format as percentage e.g. `87%`  |
| Severity              | `severity`              | Show as `7/10`                   |
| Compliance Risk       | `compliance`            | Show as `8/10`                   |
| Regulation            | `applicable_regulation` |                                  |
| Citation              | `citation`              |                                  |
| Team                  | `team`                  |                                  |
| Priority              | `priority`              | Color code: P1=red, P2=orange, P3=yellow, P4=green |
| SLA Days              | `sla_days`              | Show as `5 days`                 |
| Human Review?         | `needs_human_review`    | Show as `Yes` / `No`             |

**Do NOT show these columns in the table:**
- `severity_explanation`
- `compliance_explanation`
- `root_cause`
- `combined_results`
- `team_explanation`
- `sla_deadline`
- `remediation_steps`
- `preventative_recommendations`
- `customer_email`
- `reflection_feedback`
- `review_reasons`
- `narrative`

### Making Rows Clickable

Streamlit does not have native clickable table rows. Use this pattern:

```python
for complaint_id, data in st.session_state.complaints.items():
    cols = st.columns([...])
    # fill each column with the appropriate value
    with cols[-1]:
        if st.button("View", key=f"view_{complaint_id}"):
            st.session_state.selected_complaint = complaint_id
```

Alternatively, use `st.dataframe` with `on_select="rerun"` (Streamlit >= 1.35) for
a more polished look — but the `st.button` per row approach is simpler and works on
all versions.

### Human-in-the-Loop Buttons on Table Rows

For any row where `status == "needs_review"`, render two extra buttons directly in the
row — **Approve** and **Edit** — so the reviewer never has to open the detail view just
to action a complaint.

```python
for complaint_id, data in st.session_state.complaints.items():
    # ... render all data columns ...

    # Action column — last column
    with action_col:
        if data["status"] == "needs_review":
            approve_col, edit_col, view_col = st.columns(3)

            with approve_col:
                if st.button("✅ Approve", key=f"approve_{complaint_id}"):
                    resume_pipeline(complaint_id)   # see below
                    st.rerun()

            with edit_col:
                if st.button("✏️ Edit", key=f"edit_{complaint_id}"):
                    st.session_state[f"editing_{complaint_id}"] = True
                    st.rerun()

            with view_col:
                if st.button("View", key=f"view_{complaint_id}"):
                    st.session_state.selected_complaint = complaint_id
        else:
            if st.button("View", key=f"view_{complaint_id}"):
                st.session_state.selected_complaint = complaint_id
```

#### Inline Edit Form

When `st.session_state[f"editing_{complaint_id}"]` is `True`, render an edit form
directly below that row (use `st.expander` or an indented `st.container`):

```python
if st.session_state.get(f"editing_{complaint_id}"):
    with st.container(border=True):
        st.markdown(f"**Editing Complaint {complaint_id}** — adjust values before approving")
        st.caption(f"Review reasons: {', '.join(data['state'].get('review_reasons', []))}")

        new_severity   = st.number_input("Severity (1–10)",   min_value=1, max_value=10,
                                          value=data["state"].get("severity", 5),
                                          key=f"sev_{complaint_id}")
        new_compliance = st.number_input("Compliance (1–10)", min_value=1, max_value=10,
                                          value=data["state"].get("compliance", 5),
                                          key=f"comp_{complaint_id}")
        new_team       = st.text_input("Team",
                                        value=data["state"].get("team", ""),
                                        key=f"team_{complaint_id}")

        confirm_col, cancel_col = st.columns(2)

        with confirm_col:
            if st.button("✅ Approve with Changes", key=f"confirm_{complaint_id}"):
                overrides = {}
                if new_severity   != data["state"].get("severity"):   overrides["severity"]   = new_severity
                if new_compliance != data["state"].get("compliance"): overrides["compliance"] = new_compliance
                if new_team       != data["state"].get("team"):        overrides["team"]       = new_team
                resume_pipeline(complaint_id, overrides=overrides)
                st.session_state.pop(f"editing_{complaint_id}", None)
                st.rerun()

        with cancel_col:
            if st.button("✖ Cancel", key=f"cancel_{complaint_id}"):
                # Cancel just closes the edit form — complaint stays in needs_review
                st.session_state.pop(f"editing_{complaint_id}", None)
                st.rerun()
```

> Cancel does NOT reject the complaint. It simply closes the edit form and
> returns the row to showing the **Approve** / **Edit** / **View** buttons.

#### `resume_pipeline` helper

This function lives in `state_store.py`. It optionally applies state overrides, then
resumes the LangGraph stream in a new background thread:

```python
def resume_pipeline(complaint_id: str, overrides: dict = None):
    entry = st.session_state.complaints[complaint_id]
    graph  = entry["graph"]
    config = entry["thread_config"]

    if overrides:
        graph.update_state(config, overrides)
        entry["state"].update(overrides)

    update_complaint(complaint_id, {"status": "processing"})

    def _resume():
        try:
            for event in graph.stream(None, config=config):
                for node_name, node_output in event.items():
                    entry["agent_log"].append({
                        "agent":  AGENT_DISPLAY_NAMES.get(node_name, node_name),
                        "node":   node_name,
                        "status": "complete",
                        "output": node_output
                    })
                    entry["state"].update(node_output)
            save_complaint_to_db(complaint_id)   # persist to SQLite once fully done
            update_complaint(complaint_id, {"status": "complete"})
        except Exception as e:
            update_complaint(complaint_id, {"status": "error", "error": str(e)})

    threading.Thread(target=_resume, daemon=True).start()
```

### Status Badge Styling

Use `st.markdown` with inline HTML for colored badges:
```python
STATUS_COLORS = {
    "pending":     "#888888",
    "processing":  "#1E88E5",
    "needs_review":"#FB8C00",
    "complete":    "#43A047"
}
badge = f'<span style="background:{color};color:white;padding:2px 8px;border-radius:10px">{status}</span>'
st.markdown(badge, unsafe_allow_html=True)
```

---

## Step 6 — Background Processing with Threading

The agent pipeline must run in the background so the UI stays responsive. Use Python's
`threading` module.

### How it works

When a complaint is submitted, start a daemon thread that:
1. Calls the LangGraph `stream()` method
2. After each node completes, updates `st.session_state.complaints[complaint_id]`
3. Appends an entry to `agent_log`

```python
import threading

AGENT_DISPLAY_NAMES = {
    "validate_issue":        "Issue Validation",
    "root_cause_analysis":   "Root Cause Analysis",
    "severity_assessment":   "Severity Assessment",
    "compliance_assessment": "Compliance Assessment",
    "aggregate_results":     "Aggregating Results",
    "assign_role":           "Team Assignment",
    "review_router":         "Review Routing",
    "human_input":           "Human Review",
    "auto_proceed":          "Auto-Proceeding",
    "create_resolution":     "Resolution Planning",
    "create_customer_email": "Drafting Customer Email",
    "reflection_agent":      "Compliance Email Review",
}

def run_pipeline(complaint_id: str):
    entry = st.session_state.complaints[complaint_id]
    graph = entry["graph"]
    config = entry["thread_config"]
    input_data = entry["input"]

    try:
        update_complaint(complaint_id, {"status": "processing"})

        for event in graph.stream(input_data, config=config):
            for node_name, node_output in event.items():
                # Mark this agent as complete and update state
                log_entry = {
                    "agent": AGENT_DISPLAY_NAMES.get(node_name, node_name),
                    "node":  node_name,
                    "status": "complete",
                    "output": node_output
                }
                entry["agent_log"].append(log_entry)

                # Merge output into the running state
                entry["state"].update(node_output)

        # Check if pipeline paused for human review
        graph_state = graph.get_state(config)
        if graph_state.next == ("human_input",):
            update_complaint(complaint_id, {"status": "needs_review"})
        else:
            update_complaint(complaint_id, {"status": "complete"})

    except Exception as e:
        update_complaint(complaint_id, {"status": "error", "error": str(e)})

def start_pipeline_thread(complaint_id: str):
    thread = threading.Thread(
        target=run_pipeline,
        args=(complaint_id,),
        daemon=True
    )
    thread.start()
```

> **Important:** Streamlit's session state IS thread-safe for reads/writes from
> background threads as of v1.18+. However, the UI will NOT automatically refresh
> when a background thread updates state. You must use `st.rerun()` on a timer
> in the detail view to poll for updates (see Step 7).

---

## Step 7 — Build `components/agent_progress.py`

This is the detail panel shown when a user clicks on a complaint row.

### When to Show

If `st.session_state.selected_complaint` is set, render the detail view instead of
(or below) the table. Use a back button to return:
```python
if st.button("← Back to Dashboard"):
    st.session_state.selected_complaint = None
    st.rerun()
```

### Layout

Split into two columns: left column (60%) for agent progress, right column (40%) for
final outputs.

#### Left Column: Agent Progress

Show each agent as a step card. The order of agents in the pipeline is:

```
1.  Issue Validation          (validate_issue)
2a. Root Cause Analysis       (root_cause_analysis)       ─┐
2b. Severity Assessment       (severity_assessment)         ├─ these three run in parallel
2c. Compliance Assessment     (compliance_assessment)      ─┘
3.  Aggregating Results       (aggregate_results)
4.  Team Assignment           (assign_role)
5.  Review Routing            (review_router)
6a. [Human Review]            (human_input)               — only shown if triggered
6b. [Auto-Proceeding]         (auto_proceed)              — only shown if triggered
7a. Resolution Planning       (create_resolution)          ─┐ parallel
7b. Drafting Customer Email   (create_customer_email)      ─┘
8.  Compliance Email Review   (reflection_agent)           — may repeat up to 3x
```

For each agent, render a card with:
- An icon: ⏳ (pending), 🔄 (running), ✅ (complete), ❌ (error)
- The agent display name
- If complete: a collapsed `st.expander` showing what the agent output

To determine status of each agent:
```python
completed_nodes = {entry["node"] for entry in agent_log if entry["status"] == "complete"}
currently_running = determine_running(agent_log, completed_nodes)  # see below
```

To determine which agent is currently running: it is the agent whose name appears in
`graph.get_state(config).next`, OR the most recently started but not yet completed node
based on the thread's current position.

Since the thread updates `agent_log` only on completion, a node is "running" if
the complaint `status == "processing"` and the node has not yet appeared in `agent_log`.
The currently running node is the next expected node given what has completed.

#### Showing Parallel Agents

For the three parallel agents (root_cause, severity, compliance), render them
side-by-side using `st.columns(3)`.

#### Right Column: Final Outputs

Only render this column once `status == "complete"` or `status == "needs_review"`.

Show these fields in labeled sections using `st.metric` or `st.info` boxes:

**Classification**
- Product, Sub-Product, Issue, Sub-Issue, Confidence

**Severity**
- Score (large number), Explanation (full text here — this is where explanations go)

**Compliance**
- Score, Explanation, Regulation, Citation

**Routing**
- Team, Priority, SLA Deadline, Team Explanation

**Resolution**
- `st.expander("Resolution Plan")` → remediation steps
- `st.expander("Preventative Recommendations")` → preventative text

**Customer Email**
- `st.expander("Customer Email Draft")` → full email text
- Reflection Score badge (e.g. `4/5`)

#### Human Review Panel

If `status == "needs_review"`, render this panel prominently in an `st.warning` box
at the top of the detail view, above the agent progress cards:

```
⚠️  This complaint requires human review before proceeding.

Reasons: [list the review_reasons]

[ ✅ Approve ]   [ ✏️ Edit ]
```

**Approve button** → calls `resume_pipeline(complaint_id)` (same helper used by the
table row buttons — defined in `state_store.py`). Pipeline resumes in a background
thread, status returns to `"processing"`, detail view auto-refreshes every 1 second
until complete.

**Edit button** → sets `st.session_state[f"editing_detail_{complaint_id}"] = True`
and reruns. This renders the same edit form inline below the warning box:

```python
if st.session_state.get(f"editing_detail_{complaint_id}"):
    with st.container(border=True):
        new_severity   = st.number_input(...)
        new_compliance = st.number_input(...)
        new_team       = st.text_input(...)

        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("✅ Approve with Changes"):
                resume_pipeline(complaint_id, overrides={...})
                st.session_state.pop(f"editing_detail_{complaint_id}", None)
                st.rerun()
        with cancel_col:
            if st.button("✖ Cancel"):
                # Closes the edit form only — complaint stays in needs_review
                st.session_state.pop(f"editing_detail_{complaint_id}", None)
                st.rerun()
```

Cancel closes the edit form and returns to showing the **Approve** / **Edit** buttons.
It does NOT reject or discard the complaint.

### Auto-Refresh While Processing

When a complaint is in `"processing"` status, the UI needs to refresh to show
new agent completions. Use this pattern at the top of the detail view:

```python
if st.session_state.complaints[complaint_id]["status"] == "processing":
    import time
    time.sleep(1)  # poll every 1 second
    st.rerun()
```

This will cause Streamlit to rerun every second while the pipeline is running,
picking up any new agent_log entries from the background thread.

---

## Step 8 — Build `streamlit_app.py` (Main Entry Point)

This is the file you run with `streamlit run streamlit_app.py`.

```python
import streamlit as st
from state_store import init_store
from components.complaint_table import render_table
from components.add_complaint_modal import render_add_form
from components.agent_progress import render_detail_view

st.set_page_config(
    page_title="Resolv — Complaint Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Initialize store
init_store()

# Initialize session state flags
if "show_add_form" not in st.session_state:
    st.session_state.show_add_form = False
if "selected_complaint" not in st.session_state:
    st.session_state.selected_complaint = None

# ── Header Row ──────────────────────────────────────────────────────────────
col_title, col_btn = st.columns([8, 2])
with col_title:
    st.title("Complaint Dashboard")
    total = len(st.session_state.complaints)
    processing = sum(1 for c in st.session_state.complaints.values() if c["status"] == "processing")
    st.caption(f"{total} complaints total · {processing} currently processing")
with col_btn:
    st.write("")  # vertical spacing
    if st.button("+ Add Complaint", type="primary", use_container_width=True):
        st.session_state.show_add_form = True
        st.session_state.selected_complaint = None

# ── Add Complaint Form (sidebar) ─────────────────────────────────────────────
if st.session_state.show_add_form:
    render_add_form()

# ── Main Content Area ─────────────────────────────────────────────────────────
if st.session_state.selected_complaint:
    render_detail_view(st.session_state.selected_complaint)
else:
    if not st.session_state.complaints:
        st.info("No complaints yet. Click '+ Add Complaint' to get started.")
    else:
        render_table()
```

---

## Step 9 — Environment & Dependencies

### `.env` file (already gitignored)
```
OPENAI_API_KEY=sk-...
```

### `requirements.txt`
```
streamlit>=1.35.0
langchain-openai>=0.1.0
langchain-chroma>=0.1.0
langchain-core>=0.2.0
langchain-text-splitters>=0.2.0
langgraph>=0.1.0
python-dotenv>=1.0.0
pandas>=2.0.0
pydantic>=2.0.0
typing-extensions>=4.0.0
```

### Run the app
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

---

## Step 10 — Key Edge Cases to Handle

1. **Duplicate Complaint IDs** — before calling `add_complaint`, check if the ID already
   exists in the store. If it does, show `st.warning("Complaint ID already exists.")` and
   do not add it.

2. **Empty table** — if `st.session_state.complaints` is empty, show the info message
   instead of an empty table.

3. **Reflection loop** — the `reflection_agent` may run up to 3 times. Each pass should
   add a new entry to `agent_log` with a label like `"Compliance Email Review (attempt 2)"`.
   Show all attempts in the progress panel.

4. **Graph stream ends before human_input** — always call `graph.get_state(config).next`
   after streaming to check if the pipeline paused. Do not assume `complete` just because
   streaming stopped.

5. **CSV with missing narrative** — if any row has an empty `narrative`, skip that row
   and show a warning listing which complaint IDs were skipped.

6. **Thread safety** — do not pass the `st.session_state` object into the thread.
   Instead, pass the `complaint_id` and let the thread read from `st.session_state`
   directly (this is safe in Streamlit >= 1.18).

7. **Long narratives in CSV** — truncate the narrative to 5000 characters before passing
   to the LLM to avoid token limit errors. Show a `st.warning` if truncation occurred.

---

## Summary Flow Diagram

```
User opens app
    │
    ├─ No complaints → shows empty state + Add Complaint button
    │
    └─ Has complaints → shows dashboard table
            │
            ├─ Clicks "+ Add Complaint" → modal opens
            │       │
            │       ├─ "Add Individual Complaint" → form with cascading taxonomy dropdowns
            │       │       └─ Submit → add to SQLite (pending) → start background thread → open detail view
            │       │
            │       └─ "Upload CSV" → file uploader → parse rows → add each to SQLite → start threads
            │
            ├─ Row status = needs_review → shows [ ✅ Approve ] [ ✏️ Edit ] buttons inline on the row
            │       │
            │       ├─ Approve → resume_pipeline() → status back to processing → completes → saved to SQLite
            │       │
            │       └─ Edit → inline edit form (severity, compliance, team)
            │               ├─ Approve with Changes → apply overrides → resume_pipeline() → saved to SQLite
            │               └─ Cancel → closes form → row returns to showing Approve / Edit buttons
            │
            └─ Clicks "View" on any row → opens detail view
                    │
                    ├─ Status = processing
                    │       └─ Shows agent cards with live status → auto-refreshes every 1s
                    │
                    ├─ Status = needs_review
                    │       └─ Warning panel at top with [ ✅ Approve ] [ ✏️ Edit ] buttons
                    │               └─ Same Approve / Edit / Cancel behavior as table row
                    │
                    └─ Status = complete
                            └─ Shows all agent cards (all ✅) + full final output panel
                               (loaded from SQLite if page was refreshed)
```
