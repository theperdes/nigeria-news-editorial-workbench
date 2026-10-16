from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any

from processors.dedupe import dedupe_articles
from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_articles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_articles(path: Path, articles: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(articles, file, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dedupe review candidate news articles.")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    review_candidates_path = resolve_review_candidates_path(args.run_date)
    articles = load_articles(review_candidates_path)
    deduped_articles = dedupe_articles(articles)
    deduped_news_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "deduped_news.json", deduped_articles
    )

    duplicate_count = sum(article.get("duplicate_count", 0) for article in deduped_articles)
    print(f"Loaded {len(articles)} review candidate articles from {review_candidates_path}")
    print(f"Saved {len(deduped_articles)} deduped articles to {deduped_news_path}")
    print(f"Collapsed {duplicate_count} duplicate articles")


def resolve_review_candidates_path(run_date: str | None) -> Path:
    try:
        return resolve_pipeline_input_path(ROOT_DIR, run_date, "review_candidates.json")
    except FileNotFoundError:
        return resolve_pipeline_input_path(ROOT_DIR, run_date, "selected_news.json")


if __name__ == "__main__":
    main()
