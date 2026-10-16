from __future__ import annotations

import json
import re
import argparse
from pathlib import Path
from typing import Any

from build_digest import parse_review
from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]
FETCH_FAILED_STATUSES = {"request_failed", "extract_failed", "resolve_failed"}


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
    parser = argparse.ArgumentParser(description="Prepare reviewed articles for LLM generation.")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    review_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, "review.md")
    deduped_news_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, "deduped_news.json")
    reviewed_articles = parse_review(review_path.read_text(encoding="utf-8"))
    source_articles = load_json(deduped_news_path)
    source_index = build_source_index(source_articles)

    llm_items: list[dict[str, Any]] = []
    missing_matches: list[str] = []
    fetch_failed_skips: list[str] = []

    for reviewed_article in reviewed_articles:
        if reviewed_article["decision"] not in {"include", "maybe"}:
            continue

        source_article = match_source_article(reviewed_article, source_index)
        if not source_article:
            missing_matches.append(reviewed_article["original_title"])
            continue
        if is_fetch_failed(source_article):
            fetch_failed_skips.append(reviewed_article["original_title"])
            continue

        llm_items.append(build_llm_item(reviewed_article, source_article))

    llm_input_path = save_json_with_latest(ROOT_DIR, args.run_date, "llm_input.json", llm_items)

    print(f"Loaded {len(reviewed_articles)} reviewed articles from {review_path}")
    print(f"Prepared {len(llm_items)} LLM input items")
    if missing_matches:
        print(f"Missing source matches: {len(missing_matches)}")
        for title in missing_matches:
            print(f"- {title}")
    if fetch_failed_skips:
        print(f"Skipped reviewed fetch-failed items: {len(fetch_failed_skips)}")
        for title in fetch_failed_skips:
            print(f"- {title}")
    print(f"Saved LLM input to {llm_input_path}")


def build_source_index(articles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for article in articles:
        for key in article_keys(article):
            index[key] = article
    return index


def article_keys(article: dict[str, Any]) -> set[str]:
    keys = {
        normalize_key(article.get("title", "")),
        normalize_key(article.get("resolved_url", "")),
        normalize_key(article.get("original_url", "")),
        normalize_key(article.get("google_news_url", "")),
    }
    return {key for key in keys if key}


def match_source_article(
    reviewed_article: dict[str, str], source_index: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    for value in [
        reviewed_article.get("url", ""),
        reviewed_article.get("suggested_title", ""),
        reviewed_article.get("original_title", ""),
    ]:
        key = normalize_key(value)
        if key in source_index:
            return source_index[key]
    return None


def build_llm_item(
    reviewed_article: dict[str, str], source_article: dict[str, Any]
) -> dict[str, Any]:
    final_category = reviewed_article["final_category"] or reviewed_article["suggested_category"]
    final_title = reviewed_article["final_title"] or reviewed_article["suggested_title"]

    return {
        "decision": reviewed_article["decision"],
        "category": final_category,
        "title": final_title,
        "original_title": source_article.get("title", ""),
        "source": source_article.get("source", ""),
        "url": source_article.get("resolved_url")
        or source_article.get("original_url")
        or source_article.get("google_news_url", ""),
        "notes": reviewed_article["notes"],
        "article_text": source_article.get("article_text", ""),
        "news_type": source_article.get("news_type", ""),
        "project_stage": source_article.get("project_stage", ""),
        "entities": source_article.get("entities", {}),
        "watch_reason": source_article.get("watch_reason", ""),
        "relevance_score": source_article.get("relevance_score", 0),
        "relevance_level": source_article.get("relevance_level", ""),
        "duplicate_count": source_article.get("duplicate_count", 0),
        "duplicate_sources": source_article.get("duplicate_sources", []),
        "duplicate_titles": source_article.get("duplicate_titles", []),
        "duplicate_urls": source_article.get("duplicate_urls", []),
    }


def is_fetch_failed(article: dict[str, Any]) -> bool:
    return str(article.get("fetch_status") or "") in FETCH_FAILED_STATUSES


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


if __name__ == "__main__":
    main()
