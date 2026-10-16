from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from processors.relevance import find_matches


ROOT_DIR = Path(__file__).resolve().parents[1]
DISCOVERED_NEWS_PATH = ROOT_DIR / "data" / "discovered_news.json"
PREFILTERED_NEWS_PATH = ROOT_DIR / "data" / "prefiltered_news.json"
RULES_PATH = ROOT_DIR / "config" / "scoring_rules.yaml"

PREFILTER_NEGATIVE_TITLE_TERMS = [
    "race",
    "road race",
    "sports",
    "football",
    "crash",
    "accident",
    "celebrity",
    "heroin",
    "drug",
    "gossip",
]

SCALE_OR_STAGE_TERMS = [
    "km",
    "kilometres",
    "kilometers",
    "billion",
    "million",
    "trillion",
    "MW",
    "plant",
    "terminal",
    "corridor",
    "clearance",
    "cargo",
    "commissioned",
    "launched",
    "construction",
    "rehabilitation",
    "upgrade",
    "expansion",
    "processing",
    "funding",
    "project",
]

POLITICS_CONTEXT_TERMS = [
    "2027",
    "election",
    "INEC",
    "minister",
    "budget",
    "reform",
    "subsidy",
    "tariff",
    "debt",
    "fiscal",
    "CBN",
    "opposition",
    "coalition",
]

SECURITY_NODE_TERMS = [
    "bridge",
    "highway",
    "road",
    "rail",
    "railway",
    "pipeline",
    "port",
    "airport",
    "power",
    "project",
    "construction",
]


def main() -> None:
    discovered_items = load_json(DISCOVERED_NEWS_PATH)
    rules = load_yaml(RULES_PATH)
    prefiltered_items: list[dict[str, Any]] = []

    for item in discovered_items:
        result = prefilter_item(item, rules)
        item = dict(item)
        item.update(result)
        if result["prefilter_status"] == "selected":
            prefiltered_items.append(item)

    save_json(PREFILTERED_NEWS_PATH, prefiltered_items)
    print(f"Loaded {len(discovered_items)} discovered items")
    print(f"Saved {len(prefiltered_items)} prefiltered items to {PREFILTERED_NEWS_PATH}")


def prefilter_item(item: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(
        [
            item.get("title", ""),
            item.get("source", ""),
            item.get("rss_summary", ""),
            item.get("search_keyword", ""),
        ]
    )
    title = item.get("title", "")
    title_negative_matches = find_matches(title, PREFILTER_NEGATIVE_TITLE_TERMS)
    sector_matches = find_matches(text, rules["sector_keywords"])
    project_matches = find_matches(text, rules["project_signals"])
    financier_matches = find_matches(text, rules["financiers"])
    institution_matches = find_matches(text, rules["institutions"])
    company_matches = find_matches(text, rules["companies"])
    location_matches = find_matches(text, rules["locations"])
    scale_matches = find_matches(text, SCALE_OR_STAGE_TERMS)
    politics_matches = find_matches(text, POLITICS_CONTEXT_TERMS)
    security_matches = find_matches(text, rules["news_types"]["security_risk"])
    security_node_matches = find_matches(text, SECURITY_NODE_TERMS)
    negative_matches = find_matches(text, rules["negative_signals"])
    routine_matches = find_matches(text, rules["routine_politics_signals"])

    strong_matches = (
        project_matches
        + financier_matches
        + institution_matches
        + company_matches
    )
    matched = unique_preserving_order(
        sector_matches
        + strong_matches
        + location_matches
        + scale_matches
        + politics_matches
        + security_matches
        + security_node_matches
    )

    if title_negative_matches and not strong_matches:
        return {
            "prefilter_status": "ignored",
            "prefilter_reasons": [f"title negative: {', '.join(title_negative_matches)}"],
            "prefilter_matched_keywords": unique_preserving_order(title_negative_matches + matched),
        }

    if negative_matches and not strong_matches:
        return {
            "prefilter_status": "ignored",
            "prefilter_reasons": [f"negative only: {', '.join(negative_matches)}"],
            "prefilter_matched_keywords": negative_matches,
        }

    if routine_matches and not (strong_matches or politics_matches):
        return {
            "prefilter_status": "ignored",
            "prefilter_reasons": [f"routine politics weak match: {', '.join(routine_matches)}"],
            "prefilter_matched_keywords": routine_matches + matched,
        }

    reasons: list[str] = []
    if strong_matches:
        reasons.append(f"strong: {', '.join(unique_preserving_order(strong_matches)[:10])}")
    if sector_matches and scale_matches:
        reasons.append(
            f"sector + scale/stage: {', '.join(sector_matches[:6])} + {', '.join(scale_matches[:6])}"
        )
    if politics_matches:
        reasons.append(f"politics/fiscal context: {', '.join(politics_matches[:8])}")
    if security_matches and security_node_matches:
        reasons.append(
            f"security + node: {', '.join(security_matches[:5])} + {', '.join(security_node_matches[:5])}"
        )

    if reasons:
        return {
            "prefilter_status": "selected",
            "prefilter_reasons": reasons,
            "prefilter_matched_keywords": matched,
        }

    return {
        "prefilter_status": "ignored",
        "prefilter_reasons": ["no prefilter keywords matched"],
        "prefilter_matched_keywords": [],
    }


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def unique_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        normalized = item.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(item)
    return unique_items


if __name__ == "__main__":
    main()
