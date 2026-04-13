from __future__ import annotations

import atexit
import json
import os
import time
from collections import defaultdict
from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

try:
    from langsmith import traceable
except ImportError:
    def traceable(func):  # type: ignore[misc]
        return func

from langchain_chroma import Chroma
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY in your environment before running the app.")
if os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"):
    os.environ.setdefault("LANGSMITH_TRACING", "true")

llm = ChatOpenAI(model="gpt-5.4-mini", temperature=0.7, api_key=OPENAI_API_KEY)

with (BASE_DIR / "taxonomy.json").open("r", encoding="utf-8") as handle:
    taxonomy = json.load(handle)

embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
regulation_store = Chroma(
    collection_name="cfpb_regulations",
    embedding_function=embeddings,
    persist_directory=str(BASE_DIR / "regulation_index"),
)

CHECKPOINT_DB_PATH = BASE_DIR / "langgraph_checkpoints.db"
_checkpointer_context = SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH))
checkpointer = _checkpointer_context.__enter__()
atexit.register(lambda: _checkpointer_context.__exit__(None, None, None))


class State(TypedDict, total=False):
    complaint_id: str
    issue: str
    sub_issue: str
    product: str
    sub_product: str
    narrative: str
    valid_issue: str
    valid_sub_issue: str
    valid_product: str
    valid_sub_product: str
    confidence: float
    root_cause: str
    severity: int
    severity_explanation: str
    compliance: int
    compliance_explanation: str
    applicable_regulation: str
    citation: str
    combined_results: str
    needs_human_review: bool
    review_reasons: list[str]
    team: str
    team_explanation: str
    priority: str
    sla_days: int
    sla_deadline: str
    remediation_steps: str
    preventative_recommendations: str
    customer_email: str
    reflection_feedback: list[str]
    reflection_score: int
    reflection_passed: bool
    reflection_attempts: int


class ProductOutput(BaseModel):
    valid_product: str = Field(description="Chosen product; must be exactly one of the options.")
    confidence: float


class SubProductOutput(BaseModel):
    valid_sub_product: str = Field(description="Chosen sub-product; must be exactly one of the options.")
    confidence: float


class IssueOutput(BaseModel):
    valid_issue: str = Field(description="Chosen issue; must be exactly one of the options.")
    confidence: float


class SubIssueOutput(BaseModel):
    valid_sub_issue: str = Field(description="Chosen sub-issue; must be exactly one of the options.")
    confidence: float


class RootCauseOutput(BaseModel):
    root_cause: str = Field(description="Root cause analysis of the issue.")


class SeverityOutput(BaseModel):
    severity: int = Field(description="Severity score from 1 to 10.")
    severity_explanation: str = Field(description="Explanation justifying the severity score.")


class ComplianceOutput(BaseModel):
    compliance: int = Field(description="Compliance risk score from 1 to 10.")
    compliance_explanation: str = Field(description="Explanation justifying the compliance score.")
    applicable_regulation: str = Field(
        description="The regulation that applies (FCRA, FDCPA, TILA, ECOA, Regulation E, RESPA, Payday Rule, UDAAP, or None)."
    )
    citation: str = Field(description="The specific legal citation.")


class ReflectionOutput(BaseModel):
    passed: bool = Field(description="True if email is fully compliant, False otherwise.")
    feedback: list[str] = Field(description="Specific issues to fix. Empty if passed.")
    compliance_score: int = Field(description="Score 1-5. 5 = fully compliant.")


class TeamOutput(BaseModel):
    team: str = Field(description="The internal team that should handle this complaint.")
    team_explanation: str = Field(description="Why this team was assigned.")
    priority: str = Field(description="Priority level: P1, P2, P3, or P4.")
    sla_days: int = Field(description="Days to resolve based on severity and compliance risk.")
    sla_deadline: str = Field(description="Specific deadline sentence.")


class ResolutionOutput(BaseModel):
    remediation_steps: str = Field(description="The step-by-step process for the team to resolve the issue.")
    preventative_recommendations: str = Field(description="Recommendations to avoid the issue in the future.")


class CustomerEmailOutput(BaseModel):
    customer_email: str = Field(description="The customer email to send.")


REVIEWABLE_FIELDS = {"severity", "compliance", "team"}
_LOCAL_TIMING_BUFFER: ContextVar[dict[str, list[float]] | None] = ContextVar("local_timing_buffer", default=None)


def normalize_input_data(input_data: dict) -> dict:
    normalized = dict(input_data)
    normalized["complaint_id"] = str(normalized.get("complaint_id", "")).strip()
    for key in ["product", "sub_product", "issue", "sub_issue"]:
        normalized[key] = str(normalized.get(key, "") or "").strip()
    narrative = str(normalized.get("narrative", "") or "").strip()
    normalized["narrative"] = narrative[:5000]
    return normalized


def begin_timing_capture():
    return _LOCAL_TIMING_BUFFER.set(defaultdict(list))


def end_timing_capture(token):
    _LOCAL_TIMING_BUFFER.reset(token)


def consume_local_latency(node_name: str) -> float | None:
    buffer = _LOCAL_TIMING_BUFFER.get()
    if not buffer:
        return None
    values = buffer.get(node_name) or []
    if not values:
        return None
    return values.pop(0)


def _record_local_latency(node_name: str, elapsed_seconds: float):
    buffer = _LOCAL_TIMING_BUFFER.get()
    if buffer is None:
        return
    buffer[node_name].append(elapsed_seconds)


def _instrument_node(node_name: str, func):
    def wrapped(state: State):
        start = time.perf_counter()
        try:
            return func(state)
        finally:
            _record_local_latency(node_name, time.perf_counter() - start)

    wrapped.__name__ = getattr(func, "__name__", node_name)
    return wrapped


def _retrieve_regulations(state: State, limit: int = 4):
    query = f"{state['valid_issue']} {state['valid_sub_issue']} {state['narrative']}"
    return regulation_store.similarity_search(query, k=limit)


def _render_reg_context(results) -> str:
    return "\n\n---\n\n".join(
        [
            f"Regulation: {item.metadata['regulation']}\n"
            f"Citation: {item.metadata['citation']}\n"
            f"Text: {item.page_content}"
            for item in results
        ]
    )


@traceable
def validate_issue(state: State):

    product_llm = llm.with_structured_output(ProductOutput)
    sub_product_llm = llm.with_structured_output(SubProductOutput)
    issue_llm = llm.with_structured_output(IssueOutput)
    sub_issue_llm = llm.with_structured_output(SubIssueOutput)
    
    # Step 1 — Product (~10 normalized options)
    products = list(taxonomy.keys())
    product_result = product_llm.invoke(
        f"Given the following narrative:\n{state['narrative']}\n\nAnd the classifications:\nProduct: {state['product']}\nSub-product: {state['sub_product']}\nIssue: {state['issue']}\nSub-issue: {state['sub_issue']}\n\nAre these classifications appropriate for the narrative? If not, suggest the most accurate options from this list: {products}."
    )
    chosen_product = product_result.valid_product

    # Step 2 — Sub-product (4-12 options)
    sub_products = list(taxonomy[chosen_product].keys())
    sub_product_result = sub_product_llm.invoke(
        f"Given the following narrative:\n{state['narrative']}\n\nAnd the classifications:\nProduct: {state['product']}\nSub-product: {state['sub_product']}\nIssue: {state['issue']}\nSub-issue: {state['sub_issue']}\n\nAre these classifications appropriate for the narrative? If not, suggest the most accurate options from this list: {sub_products}."
    )
    chosen_sub_product = sub_product_result.valid_sub_product

    # Step 3 — Issue (4-16 options scoped to sub-product)
    issues = list(taxonomy[chosen_product][chosen_sub_product].keys())
    issue_result = issue_llm.invoke(
        f"Given the following narrative:\n{state['narrative']}\n\nAnd the classifications:\nProduct: {state['product']}\nSub-product: {state['sub_product']}\nIssue: {state['issue']}\nSub-issue: {state['sub_issue']}\n\nAre these classifications appropriate for the narrative? If not, suggest the most accurate options from this list: {issues}."
    )
    chosen_issue = issue_result.valid_issue

    # Step 4 — Sub-issue (2-6 options)
    sub_issues = taxonomy[chosen_product][chosen_sub_product][chosen_issue]
    sub_issue_result = sub_issue_llm.invoke(
        f"Given the following narrative:\n{state['narrative']}\n\nAnd the classifications:\nProduct: {state['product']}\nSub-product: {state['sub_product']}\nIssue: {state['issue']}\nSub-issue: {state['sub_issue']}\n\nAre these classifications appropriate for the narrative? If not, suggest the most accurate options from this list: {sub_issues}."
    )

    return {
        "valid_product": chosen_product,
        "valid_sub_product": chosen_sub_product,
        "valid_issue": chosen_issue,
        "valid_sub_issue": sub_issue_result.valid_sub_issue,
        "confidence": min(
            product_result.confidence,
            sub_product_result.confidence,
            issue_result.confidence,
            sub_issue_result.confidence
        )
    }


@traceable
def root_cause_analysis(state: State):
    structured_llm = llm.with_structured_output(RootCauseOutput)
    prompt = (
        f"Analyze the root cause of the issue: {state['valid_issue']} "
        f"for product: {state['valid_product']} "
        f"based on the narrative: {state['narrative']}"
    )
    result = structured_llm.invoke(prompt)
    return {"root_cause": result.root_cause}


@traceable
def severity_assessment(state: State):
    structured_llm = llm.with_structured_output(SeverityOutput)
    prompt = f"""You are a financial complaint severity analyst. Rate the severity of this consumer complaint on a scale of 1-10.
SEVERITY RULES:

High severity (8-10) — serious financial harm, urgent action needed:
- Fraud, scam, or identity theft
- Unauthorized transactions or account access
- Account frozen or funds inaccessible
- Foreclosure or repossession
- Incorrect credit report information causing financial damage
- Large financial loss (hundreds or thousands of dollars)
- Repeated unresolved issues causing ongoing harm
- Legal threats or debt collection misconduct

Medium severity (4-7) — real problem but manageable:
- Unexpected or incorrect fees
- Payment processing errors
- Billing disputes
- Trouble opening, managing, or closing an account
- Poor customer service causing moderate inconvenience
- Loan servicing problems
- Application delays

Low severity (1-3) — minor inconvenience, no significant financial harm:
- Confusing or misleading advertising
- Minor rewards or promotional issues
- General dissatisfaction with service
- Small disclosure complaints without direct harm
- Minor communication frustrations

ADDITIONAL FACTORS that increase severity:
- Consumer mentions being a Servicemember or veteran (+1)
- Issue has been unresolved for more than 30 days (+1)
- Consumer mentions attorney, lawsuit, or regulator (+2)
- Consumer mentions credit score impact (+1)
- Consumer expresses significant emotional distress (+1)

COMPLAINT DETAILS:
Product: {state['valid_product']}
Sub-product: {state['valid_sub_product']}
Issue: {state['valid_issue']}
Sub-issue: {state['valid_sub_issue']}
Narrative: {state['narrative']}

Rate the severity 1-10 and explain your reasoning in 1-2 sentences.
"""
    result = structured_llm.invoke(prompt)
    return {"severity": result.severity, "severity_explanation": result.severity_explanation}


@traceable
def compliance_assessment(state: State):
    structured_llm = llm.with_structured_output(ComplianceOutput)
    relevant_regs = _retrieve_regulations(state)
    reg_context = _render_reg_context(relevant_regs)

    result = structured_llm.invoke(
        f"""You are a financial compliance analyst. Assess the regulatory risk of this consumer complaint using the official CFPB regulation sections below.

OFFICIAL CFPB REGULATIONS: {reg_context}

COMPLAINT DETAILS:
Product: {state['valid_product']}
Sub-product: {state['valid_sub_product']}
Issue: {state['valid_issue']}
Sub-issue: {state['valid_sub_issue']}
Narrative: {state['narrative']}

COMPLIANCE RISK SCALE:
High (8-10): Specific regulation above was likely violated
Medium (4-7): Potential violation, regulatory gray area
Low (1-3): No clear regulatory violation

Rate compliance risk 1-10. Cite the specific regulation and section from above. Explain in 1-2 sentences."""
    )

    return {
        "compliance": result.compliance,
        "compliance_explanation": result.compliance_explanation,
        "applicable_regulation": result.applicable_regulation,
        "citation": result.citation,
    }


@traceable
def aggregate_results(state: State):
    combined = (
        f"Issue: {state['valid_issue']}\n"
        f"Product: {state['valid_product']}\n"
        f"Root Cause: {state['root_cause']}\n"
        f"Severity: {state['severity']}/10 — {state['severity_explanation']}\n"
        f"Compliance Risk: {state['compliance']}/10 — {state['compliance_explanation']}"
    )
    return {"combined_results": combined}


@traceable
def assign_role(state: State):
    structured_llm = llm.with_structured_output(TeamOutput)
    result = structured_llm.invoke(
        f"""Assign the correct internal team, priority level, and SLA deadline for this consumer complaint.

COMPLAINT DETAILS:
Product: {state['valid_product']}
Sub-product: {state['valid_sub_product']}
Issue: {state['valid_issue']}
Sub-issue: {state['valid_sub_issue']}
Severity: {state['severity']}/10
Compliance Risk: {state['compliance']}/10
Applicable Regulation: {state['applicable_regulation']}
Narrative: {state['narrative']}

INTERNAL TEAMS:
- Billing Team → billing disputes, duplicate charges, unexpected fees
- Fraud Team → fraud, scam, unauthorized transactions, identity theft
- Compliance Team → credit reporting issues, regulatory violations, Servicemember complaints
- Customer Service → account management, communication issues, closures
- Engineering Team → systemic issues flagged as affecting multiple consumers
- Loans Team → mortgage, student loan, vehicle loan, debt collection issues

PRIORITY RULES:
P1 (Critical) → severity 8-10 OR compliance 8-10 OR Servicemember tag
P2 (High)     → severity 6-7 OR compliance 6-7
P3 (Medium)   → severity 4-5 OR compliance 4-5
P4 (Low)      → severity 1-3 AND compliance 1-3

SLA RULES — assign based on highest applicable rule:
- Servicemember complaint → 1 business day (MLA requirement)
- P1 + compliance risk 8-10 → 1 business day
- P1 + compliance risk 6-7 → 3 business days
- P2 → 5 business days
- P3 → 10 business days
- P4 → 15 business days (CFPB maximum response window)
- NEVER assign more than 15 days

Format sla_deadline as: "Must respond within X business day(s) — by [reason]".
"""
    )
    return {
        "team": result.team,
        "team_explanation": result.team_explanation,
        "priority": result.priority,
        "sla_days": result.sla_days,
        "sla_deadline": result.sla_deadline,
    }


@traceable
def review_router(state: State):
    reasons: list[str] = []
    if state["confidence"] < 0.70:
        reasons.append(f"Low confidence: {state['confidence']:.0%}")
    if state["severity"] >= 8:
        reasons.append(f"High severity: {state['severity']}/10")
    if state["compliance"] >= 8:
        reasons.append(f"High compliance risk: {state['compliance']}/10")
    return {"needs_human_review": bool(reasons), "review_reasons": reasons}


@traceable
def route_decision(state: State):
    return "human_input" if state["needs_human_review"] else "auto_proceed"


@traceable
def human_input(state: State):
    decision = interrupt(
        {
            "complaint_id": state["complaint_id"],
            "team": state.get("team"),
            "severity": state.get("severity"),
            "compliance": state.get("compliance"),
            "review_reasons": state.get("review_reasons", []),
        }
    )
    if not isinstance(decision, dict):
        raise ValueError("Human review resume payload must be a dictionary.")
    if not decision.get("approved"):
        raise ValueError("Human review requires an approved decision to resume.")
    overrides = decision.get("overrides") or {}
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError("Human review overrides must be a dictionary.")
    return {key: value for key, value in overrides.items() if key in REVIEWABLE_FIELDS}


@traceable
def create_resolution(state: State):
    structured_llm = llm.with_structured_output(ResolutionOutput)
    result = structured_llm.invoke(
        f"Based on the following issue and product, create a resolution to the issue.\n"
        f"Issue: {state['valid_issue']}\n"
        f"Product: {state['valid_product']}\n"
        f"Narrative: {state['narrative']}"
    )
    return {
        "remediation_steps": result.remediation_steps,
        "preventative_recommendations": result.preventative_recommendations,
    }


@traceable
def create_customer_email(state: State):
    structured_llm = llm.with_structured_output(CustomerEmailOutput)
    feedback_section = ""
    if state.get("reflection_feedback"):
        feedback_section = (
            "PREVIOUS EMAIL FAILED COMPLIANCE REVIEW. YOU MUST FIX THESE SPECIFIC ISSUES:\n"
            + "\n".join(f"- {item}" for item in state["reflection_feedback"])
            + "\n\nDo not return the same email. Fix all issues listed above."
        )

    relevant_regs = _retrieve_regulations(state)
    reg_context = _render_reg_context(relevant_regs)

    result = structured_llm.invoke(
        f"""You are a compliance officer at a fintech company writing a regulatory-compliant customer response email. Your email will be reviewed by a compliance checker before it is sent.

OFFICIAL CFPB REGULATIONS (use these to cite consumer rights and timelines):
{reg_context}

COMPLAINT DETAILS:
Product: {state['valid_product']}
Issue: {state['valid_issue']}
Sub-issue: {state['valid_sub_issue']}
Severity: {state['severity']}/10
Applicable Regulation: {state['applicable_regulation']}
Citation: {state['citation']}
Case Reference: {state['complaint_id']}
Assigned Team: {state['team']}
Narrative: {state['narrative']}
{feedback_section}

MANDATORY REQUIREMENTS — every single one must appear in the email:

1. CASE REFERENCE — include "Case Reference: {state['complaint_id']}" in the subject line and opening paragraph
2. REGULATORY TIMELINE — state the exact investigation window:
- FCRA: "within 30 days of receiving your dispute (45 days if you provide additional information during the investigation)"
- FDCPA: "within 5 days of this notice"
- TILA: "within 2 billing cycles, not to exceed 90 days"
- Reg E: "within 10 business days, with provisional credit applied if investigation exceeds that period"
3. CONSUMER RIGHTS — explicitly state the consumer's rights under {state['citation']} in plain language.
4. CONTACT INFORMATION — include these three labeled placeholders:
[Customer Service Phone: __________]
[Customer Service Email: __________]
[Mailing Address: __________]
5. TONE — severity is {state['severity']}/10:
- 8-10: Urgent, empathetic, acknowledge seriousness immediately
- 4-7: Professional, clear, helpful
- 1-3: Friendly, informative
6. OUTCOME LANGUAGE — include this exact phrase or equivalent: "We cannot guarantee a specific outcome, however..."

ABSOLUTE PROHIBITIONS:
- Do NOT admit liability
- Do NOT promise a specific outcome
- Do NOT use vague timelines
- Do NOT imply the company is at fault before investigation completes
"""
    )
    return {"customer_email": result.customer_email}


@traceable
def reflection_agent(state: State):
    structured_llm = llm.with_structured_output(ReflectionOutput)
    relevant_regs = _retrieve_regulations(state)
    reg_context = _render_reg_context(relevant_regs)

    result = structured_llm.invoke(
        f"""You are a regulatory compliance reviewer. Grade this customer email for compliance violations. Do NOT rewrite it — only identify issues.

OFFICIAL CFPB REGULATIONS:
{reg_context}

ORIGINAL COMPLAINT:
Product: {state['valid_product']}
Issue: {state['valid_issue']}
Severity: {state['severity']}/10
Applicable Regulation: {state['applicable_regulation']}
Citation: {state['citation']}
Complaint ID: {state['complaint_id']}
Narrative: {state['narrative']}

EMAIL TO GRADE:
{state['customer_email']}

CHECK EACH ITEM — fail if ANY are missing or violated:
1. Admits liability or fault? → FAIL if yes
2. Promises specific outcome? → FAIL if yes
3. Missing timeline tied to regulation?
4. Missing case reference number {state['complaint_id']}? → FAIL if missing
5. Missing contact information for follow-up? → FAIL if missing
6. Tone inappropriate for severity {state['severity']}/10? → FAIL if yes
7. Missing citation of consumer rights under applicable regulation? → FAIL if missing
"""
    )
    return {
        "reflection_passed": result.passed,
        "reflection_feedback": result.feedback,
        "reflection_score": result.compliance_score,
        "reflection_attempts": state.get("reflection_attempts", 0) + 1,
    }


@traceable
def route_reflection(state: State):
    if state.get("reflection_attempts", 0) >= 3:
        return "end"
    if state["reflection_passed"]:
        return "end"
    return "retry"


@traceable
def auto_proceed(state: State):
    return {}


def build_graph():
    graph = StateGraph(State)
    graph.add_node("validate_issue", _instrument_node("validate_issue", validate_issue))
    graph.add_node("root_cause_analysis", _instrument_node("root_cause_analysis", root_cause_analysis))
    graph.add_node("severity_assessment", _instrument_node("severity_assessment", severity_assessment))
    graph.add_node("compliance_assessment", _instrument_node("compliance_assessment", compliance_assessment))
    graph.add_node("aggregate_results", _instrument_node("aggregate_results", aggregate_results))
    graph.add_node("assign_role", _instrument_node("assign_role", assign_role))
    graph.add_node("review_router", _instrument_node("review_router", review_router))
    graph.add_node("auto_proceed", _instrument_node("auto_proceed", auto_proceed))
    graph.add_node("human_input", _instrument_node("human_input", human_input))
    graph.add_node("create_resolution", _instrument_node("create_resolution", create_resolution))
    graph.add_node("create_customer_email", _instrument_node("create_customer_email", create_customer_email))
    graph.add_node("reflection_agent", _instrument_node("reflection_agent", reflection_agent))

    graph.add_edge(START, "validate_issue")
    graph.add_edge("validate_issue", "root_cause_analysis")
    graph.add_edge("validate_issue", "severity_assessment")
    graph.add_edge("validate_issue", "compliance_assessment")
    graph.add_edge("root_cause_analysis", "aggregate_results")
    graph.add_edge("severity_assessment", "aggregate_results")
    graph.add_edge("compliance_assessment", "aggregate_results")
    graph.add_edge("aggregate_results", "assign_role")
    graph.add_edge("assign_role", "review_router")
    graph.add_conditional_edges(
        "review_router",
        route_decision,
        {"human_input": "human_input", "auto_proceed": "auto_proceed"},
    )
    graph.add_edge("human_input", "create_resolution")
    graph.add_edge("human_input", "create_customer_email")
    graph.add_edge("auto_proceed", "create_resolution")
    graph.add_edge("auto_proceed", "create_customer_email")
    graph.add_edge("create_customer_email", "reflection_agent")
    graph.add_conditional_edges(
        "reflection_agent",
        route_reflection,
        {"retry": "create_customer_email", "end": END},
    )
    graph.add_edge("create_resolution", END)
    graph.add_edge("create_customer_email", END)
    return graph.compile(checkpointer=checkpointer)


def get_checkpointer():
    return checkpointer


def has_checkpoint(config: dict) -> bool:
    return get_checkpointer().get_tuple(config) is not None


def process_complaint(input_data: dict, *, graph=None, config: dict | None = None, resume: bool = False):
    compiled_graph = graph or build_graph()
    normalized_input = normalize_input_data(input_data)
    thread_id = normalized_input["complaint_id"] or "complaint"
    graph_config = config or {"configurable": {"thread_id": thread_id}}
    payload = Command(resume={"approved": True, "overrides": {}}) if resume else normalized_input

    events = []
    current_state: dict = {}
    for event in compiled_graph.stream(payload, config=graph_config):
        events.append(event)
        for output in event.values():
            current_state.update(output)

    graph_state = compiled_graph.get_state(graph_config)
    if getattr(graph_state, "values", None):
        current_state.update(graph_state.values)

    next_nodes = tuple(graph_state.next) if getattr(graph_state, "next", None) else ()
    return {
        "events": events,
        "state": current_state,
        "next": next_nodes,
        "graph": compiled_graph,
        "config": graph_config,
    }
