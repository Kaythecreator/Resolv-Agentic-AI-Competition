from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
with (BASE_DIR / "taxonomy.json").open("r", encoding="utf-8") as handle:
    TAXONOMY = json.load(handle)

INTERNAL_TEAMS = [
    "Billing Team",
    "Fraud Team",
    "Compliance Team",
    "Customer Service",
    "Engineering Team",
    "Loans Team",
]

PRIORITY_OPTIONS = ["P1", "P2", "P3", "P4"]

SLA_OPTIONS = [1, 3, 5, 10, 15]


def get_products() -> list[str]:
    return list(TAXONOMY.keys())


def get_sub_products(product: str) -> list[str]:
    return list(TAXONOMY.get(product, {}).keys())


def get_issues(product: str, sub_product: str) -> list[str]:
    return list(TAXONOMY.get(product, {}).get(sub_product, {}).keys())


def get_sub_issues(product: str, sub_product: str, issue: str) -> list[str]:
    return TAXONOMY.get(product, {}).get(sub_product, {}).get(issue, [])


def get_internal_teams() -> list[str]:
    return INTERNAL_TEAMS.copy()


def get_priorities() -> list[str]:
    return PRIORITY_OPTIONS.copy()


def get_sla_options() -> list[int]:
    return SLA_OPTIONS.copy()
