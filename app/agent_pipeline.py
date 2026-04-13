from __future__ import annotations

import atexit
import json
import os
import re
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
    compliance_citation_confidence: float
    compliance_requires_human_review: bool
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
    final_approval_required: bool
    final_approval_reasons: list[str]
    final_approved: bool


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
    citation_confidence: float = Field(description="Confidence from 0 to 1 that the chosen citation directly fits the complaint facts.")
    requires_human_review: bool = Field(description="True if the legal grounding is weak, indirect, or uncertain.")


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


class CustomerEmailSectionsOutput(BaseModel):
    subject_line: str = Field(description="Email subject line including the case reference.")
    opening_paragraph: str = Field(
        description="Opening paragraph acknowledging the complaint, case reference, and seriousness without admitting liability."
    )
    rights_paragraph: str = Field(
        description="Paragraph explaining the consumer's rights under the applicable citation in plain language."
    )
    next_steps_paragraph: str = Field(
        description="Paragraph describing the investigation/review next steps and resolution context without promising a specific outcome."
    )
    closing_paragraph: str = Field(description="Professional closing paragraph before signature.")


TRIAGE_REVIEWABLE_FIELDS = {
    "valid_product",
    "valid_sub_product",
    "valid_issue",
    "valid_sub_issue",
    "severity",
    "compliance",
    "team",
    "priority",
    "sla_days",
    "sla_deadline",
}
FINAL_APPROVAL_REVIEWABLE_FIELDS = {
    "customer_email",
    "remediation_steps",
    "preventative_recommendations",
    "team",
    "priority",
    "sla_days",
    "sla_deadline",
}
_LOCAL_TIMING_BUFFER: ContextVar[dict[str, list[float]] | None] = ContextVar("local_timing_buffer", default=None)


def _manual_sla_deadline(sla_days: int) -> str:
    return f"Must respond within {sla_days} business day(s) — manually approved by human reviewer."


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


def _normalize_text(s: str) -> str:
    return " ".join(s.split()).strip().lower()


def _infer_part_filters(valid_product: str, valid_sub_product: str) -> list[str]:
    p = (valid_product or "").lower()
    sp = (valid_sub_product or "").lower()
    if "credit reporting" in p or "credit reporting" in sp:
        return ["12 CFR Part 1022"]
    if "debt collection" in p or "debt collection" in sp:
        return ["12 CFR Part 1006"]
    if "money transfer" in p or "electronic" in p or "wallet" in p or "wallet" in sp:
        return ["12 CFR Part 1005"]
    if "mortgage" in p or "real estate settlement" in p:
        return ["12 CFR Part 1024"]
    if "credit card" in sp or "loan" in p or "line of credit" in sp:
        return ["12 CFR Part 1026", "12 CFR Part 1002"]
    return []


def _build_compliance_base_query(state: State) -> str:
    return (
        f"Product: {state['valid_product']}\n"
        f"Sub-product: {state['valid_sub_product']}\n"
        f"Issue: {state['valid_issue']}\n"
        f"Sub-issue: {state['valid_sub_issue']}\n"
        f"Narrative: {state['narrative']}"
    )


def _rewrite_regulation_query(state: State) -> str:
    base_query = _build_compliance_base_query(state)
    rewrite_prompt = f"""
Rewrite this complaint into ONE concise CFPB legal retrieval query.
Rules:
- Use only facts present in the complaint.
- Include likely regulation keywords only if supported.
- Max 30 words.
- Return only the query text.

Complaint:
{base_query}
""".strip()
    try:
        rewritten = llm.invoke(rewrite_prompt).content.strip()
        return rewritten or base_query
    except Exception:
        return base_query


def _dedupe_results(results):
    seen = set()
    deduped = []
    for item in results:
        citation = item.metadata.get("citation", "")
        text_key = _normalize_text(item.page_content[:300])
        key = (citation, text_key)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _retrieve_compliance_regulations(state: State, k: int = 6, fetch_k: int = 40, lambda_mult: float = 0.4):
    query = _rewrite_regulation_query(state)
    parts = _infer_part_filters(state["valid_product"], state["valid_sub_product"])
    search_kwargs = {
        "query": query,
        "k": k,
        "fetch_k": fetch_k,
        "lambda_mult": lambda_mult,
    }
    if parts:
        search_kwargs["filter"] = {"part": parts[0]} if len(parts) == 1 else {"$or": [{"part": x} for x in parts]}
    try:
        raw_results = regulation_store.max_marginal_relevance_search(**search_kwargs)
    except Exception:
        raw_results = _retrieve_regulations(state, limit=k)
    return query, _dedupe_results(raw_results)


def _infer_regulation_family(state: State) -> str:
    text = " ".join(
        [
            str(state.get("valid_product", "") or ""),
            str(state.get("valid_sub_product", "") or ""),
            str(state.get("valid_issue", "") or ""),
            str(state.get("valid_sub_issue", "") or ""),
            str(state.get("narrative", "") or ""),
        ]
    ).lower()
    rules = [
        ("FDCPA", ["debt collection", "collector", "debt", "time-barred", "very old debt", "threatened to sue"]),
        ("Regulation E", ["unauthorized transaction", "zelle", "wallet", "debit", "atm", "electronic fund", "funds inaccessible"]),
        ("FCRA", ["credit report", "credit reporting", "consumer report", "score", "incorrect information on your report"]),
        ("TILA", ["credit card", "billing dispute", "statement", "apr", "loan disclosure", "finance charge"]),
        ("RESPA", ["mortgage", "escrow", "servicing", "closing", "loan estimate"]),
        ("ECOA", ["discrimination", "denied credit", "adverse action"]),
        ("Payday Rule", ["payday loan", "advance loan", "rollover"]),
    ]
    for family, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return family
    return "None"


def _retrieve_regulations_for_family(state: State, family: str, limit: int = 4):
    broader_results = _retrieve_regulations(state, limit=max(limit * 2, 8))
    if family in {"", "None"}:
        return broader_results[:limit]
    filtered = [
        item
        for item in broader_results
        if family.lower() in str(item.metadata.get("regulation", "")).lower()
    ]
    return (filtered or broader_results)[:limit]


def _render_reg_context(results) -> str:
    return "\n\n---\n\n".join(
        [
            f"Regulation: {item.metadata['regulation']}\n"
            f"Citation: {item.metadata['citation']}\n"
            f"Text: {item.page_content}"
            for item in results
        ]
    )


def get_rag_results(state: State, limit: int = 4) -> list[dict[str, str]]:
    _, results = _retrieve_compliance_regulations(state, k=limit, fetch_k=max(limit * 8, 20), lambda_mult=0.4)
    return [
        {
            "regulation": str(item.metadata.get("regulation", "")),
            "citation": str(item.metadata.get("citation", "")),
            "text": item.page_content,
        }
        for item in results
    ]


def get_rag_debug_payload(state: State, limit: int = 4) -> dict[str, object]:
    query, results = _retrieve_compliance_regulations(state, k=limit, fetch_k=max(limit * 8, 20), lambda_mult=0.4)
    return {
        "rag_query": query,
        "rag_results": [
            {
                "regulation": str(item.metadata.get("regulation", "")),
                "citation": str(item.metadata.get("citation", "")),
                "part": str(item.metadata.get("part", "")),
                "block_index": item.metadata.get("block_index"),
                "subchunk_index": item.metadata.get("subchunk_index"),
                "text": item.page_content,
            }
            for item in results
        ],
    }


def _select_regulatory_snippets(state: State, limit: int = 4) -> list[dict[str, str]]:
    matches = get_rag_results(state, limit=limit)
    citation = str(state.get("citation", "") or "").strip().lower()
    regulation = str(state.get("applicable_regulation", "") or "").strip().lower()
    prioritized = [
        item
        for item in matches
        if citation and citation in item.get("citation", "").lower()
        or regulation and regulation in item.get("regulation", "").lower()
    ]
    if prioritized:
        return prioritized[:2]
    return matches[:2]


def _render_selected_reg_context(state: State) -> str:
    results = _select_regulatory_snippets(state)
    if not results:
        return "No regulation snippets retrieved."
    return "\n\n---\n\n".join(
        [
            f"Regulation: {item['regulation']}\nCitation: {item['citation']}\nText: {item['text']}"
            for item in results
        ]
    )


def _email_policy_facts(state: State) -> dict[str, str]:
    regulation = str(state.get("applicable_regulation", "") or "").strip().lower()
    citation = str(state.get("citation", "") or "").strip()
    timeline_map = {
        "fcra": "within 30 days of receiving your dispute (45 days if you provide additional information during the investigation)",
        "fdcpa": "within 5 days of this notice",
        "tila": "within 2 billing cycles, not to exceed 90 days",
        "regulation e": "within 10 business days, with provisional credit applied if investigation exceeds that period",
        "reg e": "within 10 business days, with provisional credit applied if investigation exceeds that period",
        "ecoa": "within 30 days where adverse action notice or related response timing rules apply",
        "respa": "within the applicable servicing error resolution or information request window under RESPA",
        "payday rule": "within the applicable timeframe required by the governing servicing and disclosure rules",
        "udaap": "we will review this matter promptly under applicable consumer protection requirements",
        "none": "we will review this matter promptly and in accordance with applicable requirements",
    }
    exact_timeline = timeline_map.get(regulation, "we will review this matter promptly and in accordance with applicable requirements")
    severity = int(state.get("severity", 5) or 5)
    if severity >= 8:
        tone = "Urgent, empathetic, and serious."
    elif severity >= 4:
        tone = "Professional, clear, and helpful."
    else:
        tone = "Friendly, informative, and concise."
    no_clear_citation = not citation or citation.lower() in {"none", "n/a", "unknown"}
    return {
        "exact_timeline": exact_timeline,
        "tone": tone,
        "required_phrase": "We cannot guarantee a specific outcome, however...",
        "contact_block": "[Customer Service Phone: __________]\n[Customer Service Email: __________]\n[Mailing Address: __________]",
        "no_clear_citation": "true" if no_clear_citation else "false",
    }


def _assemble_customer_email(state: State, sections: CustomerEmailSectionsOutput, policy: dict[str, str]) -> str:
    subject_line = sections.subject_line.strip()
    if f"Case Reference: {state['complaint_id']}" not in subject_line:
        subject_line = f"Case Reference: {state['complaint_id']} — {subject_line}".strip(" —")

    body_parts = [
        sections.opening_paragraph.strip(),
        (
            f"We will review this matter {policy['exact_timeline']}. "
            f"{policy['required_phrase']}"
        ),
        sections.rights_paragraph.strip(),
        sections.next_steps_paragraph.strip(),
        "You may use the following contact information for follow-up:",
        policy["contact_block"],
        sections.closing_paragraph.strip(),
        f"Sincerely,\n{state.get('team', 'Compliance Team')}",
    ]
    return f"Subject: {subject_line}\n\n" + "\n\n".join(part for part in body_parts if part)


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
    inferred_family = _infer_regulation_family(state)
    _, relevant_regs = _retrieve_compliance_regulations(state, k=6, fetch_k=40, lambda_mult=0.4)
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
HEURISTIC REGULATION FAMILY PRIOR: {inferred_family}

COMPLIANCE RISK SCALE:
High (8-10): Specific regulation above was likely violated
Medium (4-7): Potential violation, regulatory gray area
Low (1-3): No clear regulatory violation

STRICT CITATION RULES:
1. Only cite a specific section if one of the provided snippets directly matches the complaint facts.
2. If the retrieved evidence is only indirect, broad, or weakly related, set:
- applicable_regulation = "{inferred_family}" if that family is still directionally right, otherwise "None"
- citation = "None"
- citation_confidence < 0.60
- requires_human_review = true
3. Do not guess a precise citation just because it is nearby semantically.
4. In the explanation, explicitly say when the connection is indirect or uncertain.

Return:
- compliance score 1-10
- applicable_regulation
- citation
- citation_confidence (0-1)
- requires_human_review
- explanation in 1-2 sentences.
"""
    )

    return {
        "compliance": result.compliance,
        "compliance_explanation": result.compliance_explanation,
        "applicable_regulation": result.applicable_regulation,
        "citation": result.citation,
        "compliance_citation_confidence": result.citation_confidence,
        "compliance_requires_human_review": result.requires_human_review,
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
    if state.get("compliance_requires_human_review"):
        reasons.append("Compliance citation evidence is weak or uncertain")
    elif float(state.get("compliance_citation_confidence") or 0.0) < 0.60 and state.get("citation") not in {None, "", "None"}:
        reasons.append(f"Low citation confidence: {float(state.get('compliance_citation_confidence') or 0.0):.0%}")
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
    sanitized = {key: value for key, value in overrides.items() if key in TRIAGE_REVIEWABLE_FIELDS}
    if "sla_days" in sanitized and "sla_deadline" not in sanitized:
        sanitized["sla_deadline"] = _manual_sla_deadline(int(sanitized["sla_days"]))
    return sanitized


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
    structured_llm = llm.with_structured_output(CustomerEmailSectionsOutput)
    feedback_section = "No prior review failures."
    if state.get("reflection_feedback"):
        feedback_section = (
            "PREVIOUS EMAIL FAILED COMPLIANCE REVIEW. FIX ONLY THE FAILED AREAS WHILE PRESERVING ANY VALID CONTENT:\n"
            + "\n".join(f"- {item}" for item in state["reflection_feedback"])
            + f"\n\nPREVIOUS DRAFT:\n{state.get('customer_email', '')}"
        )

    reg_context = _render_selected_reg_context(state)
    policy = _email_policy_facts(state)

    sections = structured_llm.invoke(
        f"""You are drafting sections for a regulatory-compliant customer response email. Do not output a full email body; only fill the requested sections.

USE ONLY THESE REGULATORY SNIPPETS FOR CONSUMER RIGHTS LANGUAGE:
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
RESOLUTION PLAN:
{state.get('remediation_steps', '')}
PREVENTATIVE RECOMMENDATIONS:
{state.get('preventative_recommendations', '')}

FIXED POLICY FACTS THAT WILL BE INSERTED IN THE FINAL EMAIL:
- Exact timeline sentence: {policy['exact_timeline']}
- Required outcome phrase: {policy['required_phrase']}
- Contact block:
{policy['contact_block']}
- Required tone: {policy['tone']}
- No clear cited regulation: {policy['no_clear_citation']}

MANDATORY REQUIREMENTS FOR YOUR SECTION OUTPUTS:
1. `subject_line` must include "Case Reference: {state['complaint_id']}"
2. `opening_paragraph` must mention the complaint issue and case reference, but must not admit liability
3. If `No clear cited regulation` is false, `rights_paragraph` must explain the consumer's rights under {state['citation']} in plain language using the supplied regulatory snippets
4. If `No clear cited regulation` is true, `rights_paragraph` must explicitly say that no specific violated regulation has yet been confirmed and explain the consumer's right to request review/investigation in plain language without inventing a citation
5. `next_steps_paragraph` must reference the resolution plan context and investigation/review next steps without promising a specific outcome
6. `closing_paragraph` should be brief and professional
7. Do not restate the contact block or exact timeline text; those will be inserted later
8. Keep each paragraph concise and practical

ABSOLUTE PROHIBITIONS:
- Do NOT admit liability
- Do NOT promise a specific outcome
- Do NOT imply the company is at fault before investigation completes

REVIEW FEEDBACK CONTEXT:
{feedback_section}
"""
    )
    return {"customer_email": _assemble_customer_email(state, sections, policy)}


@traceable
def reflection_agent(state: State):
    structured_llm = llm.with_structured_output(ReflectionOutput)
    reg_context = _render_selected_reg_context(state)
    policy = _email_policy_facts(state)

    result = structured_llm.invoke(
        f"""You are a regulatory compliance reviewer. Grade this customer email for compliance violations. Do NOT rewrite it — only identify issues.

OFFICIAL CFPB REGULATORY SNIPPETS:
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

EXPECTED FIXED REQUIREMENTS:
- Case reference required: Case Reference: {state['complaint_id']}
- Exact timeline phrase required: {policy['exact_timeline']}
- Required outcome phrase required: {policy['required_phrase']}
- Contact placeholders required exactly:
{policy['contact_block']}
- Required tone: {policy['tone']}
- No clear cited regulation: {policy['no_clear_citation']}

CHECKLIST — fail if ANY item is missing or violated:
1. Admits liability or fault? → FAIL if yes
2. Promises specific outcome? → FAIL if yes
3. Missing the exact required timeline phrase? → FAIL if yes
4. Missing case reference number {state['complaint_id']} in subject/opening? → FAIL if yes
5. Missing any required contact placeholder? → FAIL if yes
6. Tone inappropriate for severity {state['severity']}/10? → FAIL if yes
7. If `No clear cited regulation` is false, missing explanation of consumer rights under {state['citation']} in plain language? → FAIL if yes
8. If `No clear cited regulation` is true, FAIL only if the email falsely claims a confirmed regulation/citation or invents legal rights not supported by the complaint context
9. Resolution/next-steps language inconsistent with the complaint context or remediation plan? → FAIL if yes

Return only concrete failed checks in feedback. If something passes, do not mention it.
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
        return "post_review"
    if state["reflection_passed"]:
        return "post_review"
    return "retry"


@traceable
def final_review_router(state: State):
    reasons: list[str] = []
    if state.get("severity", 0) >= 8:
        reasons.append(f"High severity: {state['severity']}/10")
    if state.get("compliance", 0) >= 8:
        reasons.append(f"High compliance risk: {state['compliance']}/10")
    if not state.get("reflection_passed", False):
        reasons.append("Customer email did not pass automated compliance review")
    return {"final_approval_required": bool(reasons), "final_approval_reasons": reasons}


@traceable
def route_final_review(state: State):
    return "final_approval" if state.get("final_approval_required") else "end"


@traceable
def final_approval(state: State):
    decision = interrupt(
        {
            "complaint_id": state["complaint_id"],
            "customer_email": state.get("customer_email"),
            "remediation_steps": state.get("remediation_steps"),
            "preventative_recommendations": state.get("preventative_recommendations"),
            "team": state.get("team"),
            "priority": state.get("priority"),
            "sla_days": state.get("sla_days"),
            "sla_deadline": state.get("sla_deadline"),
            "final_approval_reasons": state.get("final_approval_reasons", []),
        }
    )
    if not isinstance(decision, dict):
        raise ValueError("Final approval resume payload must be a dictionary.")
    if not decision.get("approved"):
        raise ValueError("Final approval requires an approved decision to resume.")
    overrides = decision.get("overrides") or {}
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError("Final approval overrides must be a dictionary.")
    sanitized = {key: value for key, value in overrides.items() if key in FINAL_APPROVAL_REVIEWABLE_FIELDS}
    if "sla_days" in sanitized and "sla_deadline" not in sanitized:
        sanitized["sla_deadline"] = _manual_sla_deadline(int(sanitized["sla_days"]))
    sanitized["final_approved"] = True
    return sanitized


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
    graph.add_node("final_review_router", _instrument_node("final_review_router", final_review_router))
    graph.add_node("final_approval", _instrument_node("final_approval", final_approval))

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
    graph.add_edge("auto_proceed", "create_resolution")
    graph.add_edge("create_resolution", "create_customer_email")
    graph.add_edge("create_customer_email", "reflection_agent")
    graph.add_conditional_edges(
        "reflection_agent",
        route_reflection,
        {"retry": "create_customer_email", "post_review": "final_review_router"},
    )
    graph.add_conditional_edges(
        "final_review_router",
        route_final_review,
        {"final_approval": "final_approval", "end": END},
    )
    graph.add_edge("final_approval", END)
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
