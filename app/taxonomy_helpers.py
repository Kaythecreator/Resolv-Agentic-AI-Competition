from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
with (BASE_DIR / "taxonomy.json").open("r", encoding="utf-8") as handle:
    TAXONOMY = json.load(handle)


def get_products() -> list[str]:
    return list(TAXONOMY.keys())


def get_sub_products(product: str) -> list[str]:
    return list(TAXONOMY.get(product, {}).keys())


def get_issues(product: str, sub_product: str) -> list[str]:
    return list(TAXONOMY.get(product, {}).get(sub_product, {}).keys())


def get_sub_issues(product: str, sub_product: str, issue: str) -> list[str]:
    return TAXONOMY.get(product, {}).get(sub_product, {}).get(issue, [])
