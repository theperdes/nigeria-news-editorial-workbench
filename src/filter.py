from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

import argparse
from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest
from processors.relevance import score_article
from source_priority import source_priority_score


ROOT_DIR = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT_DIR / "config" / "scoring_rules.yaml"
ENTITIES_PATH = ROOT_DIR / "config" / "entities.yaml"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def merge_entities(rules: dict[str, Any]) -> None:
    """Fold config/entities.yaml (curated named-entity whitelist) into the
    scoring rules, so companies/financiers/institutions/China actors live in one
    source shared with rss_prescreen. No-op if the file is absent."""
    if not ENTITIES_PATH.exists():
        return
    ents = load_yaml(ENTITIES_PATH) or {}

    def _union(existing: list[Any], extra: Any) -> list[Any]:
        seen = {str(x).casefold() for x in existing}
        out = list(existing)
        for item in extra or []:
            if str(item).casefold() not in seen:
                out.append(item)
                seen.add(str(item).casefold())
        return out

    for ent_key, rule_key in (
        ("companies", "companies"),
        ("financiers", "financiers"),
        ("institutions", "institutions"),
    ):
        rules[rule_key] = _union(rules.get(rule_key) or [], ents.get(ent_key, []))
    china = rules.setdefault("china_signals", {})
    china["actors"] = _union(china.get("actors") or [], ents.get("china_actors", []))


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score and classify fetched news articles.")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = load_yaml(RULES_PATH)
    merge_entities(rules)
    raw_news_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, "raw_news.json")

    raw_articles = load_json(raw_news_path)
    scored_articles: list[dict[str, Any]] = []

    for article in raw_articles:
        try:
            scored_articles.append(score_article(article, rules))
        except Exception as exc:
            failed_article = dict(article)
            failed_article.update(
                {
                    "relevance_score": 0,
                    "relevance_level": "below_threshold",
                    "relevance_reasons": [f"scoring failed: {exc}"],
                    "matched_keywords": [],
                    "review_candidate_status": "candidate",
                }
            )
            scored_articles.append(failed_article)

    review_candidates = select_articles_for_review(scored_articles, rules)

    scored_news_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "scored_news.json", scored_articles
    )
    review_candidates_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "review_candidates.json", review_candidates
    )
    write_legacy_selected_copy(review_candidates_path)

    print(f"Loaded {len(raw_articles)} articles from {raw_news_path}")
    print(f"Saved {len(scored_articles)} scored articles to {scored_news_path}")
    print(f"Saved {len(review_candidates)} review candidate articles to {review_candidates_path}")
    print("Synced legacy selected_news.json copy for backward compatibility")


def select_articles_for_review(
    scored_articles: list[dict[str, Any]], rules: dict[str, Any]
) -> list[dict[str, Any]]:
    candidates = [
        article
        for article in scored_articles
        if article.get("freshness_status") != "stale"
    ]
    return sorted(candidates, key=selection_sort_key, reverse=True)


def is_reviewable(article: dict[str, Any]) -> bool:
    if article.get("fetch_status") != "success":
        return False
    if article.get("freshness_status") == "stale":
        return False
    if article.get("news_type") == "irrelevant" and not article.get("value_signals"):
        return False
    return True


def mark_minimum_backfill(article: dict[str, Any], lane: str) -> dict[str, Any]:
    backfilled_article = dict(article)
    reasons = list(backfilled_article.get("relevance_reasons", []))
    reasons.append(f"selection minimum backfill for {lane}")
    backfilled_article.update(
        {
            "review_candidate_status": "candidate",
            "relevance_level": "low",
            "selection_backfill": True,
            "relevance_reasons": reasons,
        }
    )
    return backfilled_article


def write_legacy_selected_copy(review_candidates_path: Path) -> None:
    dated_selected_path = review_candidates_path.with_name("selected_news.json")
    shutil.copyfile(review_candidates_path, dated_selected_path)
    latest_selected_path = ROOT_DIR / "data" / "selected_news.json"
    latest_selected_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(review_candidates_path, latest_selected_path)


def selection_sort_key(article: dict[str, Any]) -> tuple[int, int, int, int, int]:
    priority_rank = {"high": 3, "medium": 2, "low": 1}.get(
        str(article.get("llm_prefilter_priority", "")).casefold(), 0
    )
    signal_count = len(article.get("value_signals", []))
    source_score = source_priority_score(article)
    score = int(article.get("relevance_score", 0) or 0)
    has_amount = 1 if article.get("entities", {}).get("amounts") else 0
    return (priority_rank, signal_count, source_score, score, has_amount)


if __name__ == "__main__":
    main()
