from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy

from app.agent_pipeline import build_graph, normalize_input_data
from app.db import init_db, upsert_complaint


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

HEADER_ALIASES = {
    "Complaint ID": "complaint_id",
    "Product": "product",
    "Sub-product": "sub_product",
    "Issue": "issue",
    "Sub-issue": "sub_issue",
    "Consumer complaint narrative": "narrative",
}


def _normalize_row_keys(row: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        mapped_key = HEADER_ALIASES.get(key, key)
        normalized[mapped_key] = value
    return normalized


def _process_row(row: dict[str, str]) -> tuple[str | None, str]:
    normalized = normalize_input_data(_normalize_row_keys(row))
    complaint_id = normalized["complaint_id"]
    if not complaint_id:
        return None, "skipped"

    graph = build_graph()
    config = {"configurable": {"thread_id": complaint_id}}
    state: dict = {}
    agent_log: list[dict] = []
    status = "processing"
    error = None

    try:
        for event in graph.stream(normalized, config=config):
            for node_name, node_output in event.items():
                display_name = AGENT_DISPLAY_NAMES.get(node_name, node_name)
                if node_name == "reflection_agent":
                    attempt = node_output.get("reflection_attempts")
                    if attempt:
                        display_name = f"{display_name} (attempt {attempt})"
                agent_log.append(
                    {
                        "agent": display_name,
                        "node": node_name,
                        "status": "complete",
                        "output": node_output,
                    }
                )
                state.update(node_output)

        graph_state = graph.get_state(config)
        if getattr(graph_state, "values", None):
            state.update(graph_state.values)
        status = "needs_review" if tuple(graph_state.next or ()) == ("human_input",) else "complete"
    except Exception as exc:
        error = str(exc)
        status = "error"

    upsert_complaint(
        {
            "complaint_id": complaint_id,
            "input": deepcopy(normalized),
            "status": status,
            "state": deepcopy(state),
            "agent_log": deepcopy(agent_log),
            "error": error,
            "error_traceback": None,
        }
    )
    return complaint_id, status


def main():
    parser = argparse.ArgumentParser(description="Process a CSV of complaints through the complaint pipeline.")
    parser.add_argument("csv_path", help="Path to input CSV.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of rows to process.")
    parser.add_argument("--workers", type=int, default=4, help="Number of complaints to process in parallel.")
    args = parser.parse_args()

    init_db()
    with open(args.csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if args.limit is not None:
        rows = rows[: args.limit]

    workers = max(1, args.workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_process_row, row) for row in rows]
        for future in as_completed(futures):
            complaint_id, status = future.result()
            if complaint_id is None:
                continue
            print(f"{complaint_id}: {status}")


if __name__ == "__main__":
    main()
