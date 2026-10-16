from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import yaml

from extractors.article import extract_article
from extractors.google_news_url import resolve_google_news_url
from pipeline_paths import add_run_date_arg, effective_run_date, run_data_dir, run_path, save_json_with_latest
from processors.freshness import assess_freshness

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT_DIR = Path(__file__).resolve().parents[1]

# Persist partial progress every N fetched articles so a crash can resume via
# --append, without rewriting the whole raw_news.json after every single
# article (which would make total write volume grow as O(n^2)).
CHECKPOINT_EVERY = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch full article text for prefiltered news.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--append", action="store_true", help="Append to existing raw_news.json and skip already fetched URLs.")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_date = effective_run_date(args.run_date)
    config = load_config()
    collection_config = config["collection"]
    llm_prefiltered_path = run_path(ROOT_DIR, run_date, "llm_prefiltered_news.json")
    prefiltered_path = run_path(ROOT_DIR, run_date, "prefiltered_news.json")
    input_path = llm_prefiltered_path if llm_prefiltered_path.exists() else prefiltered_path
    if not input_path.exists():
        raise FileNotFoundError(
            f"Expected llm_prefiltered_news.json or prefiltered_news.json in run directory: "
            f"{run_data_dir(ROOT_DIR, run_date)}"
        )
    items = load_json(input_path)
    if args.limit is not None:
        items = items[: args.limit]
    raw_news_path = run_path(ROOT_DIR, run_date, "raw_news.json")
    existing_items = load_json(raw_news_path) if args.append else []
    seen_urls = existing_url_keys(existing_items)
    items = [item for item in items if not article_url_keys(item) & seen_urls]

    fetched_items: dict[int, dict[str, Any]] = {}
    index_to_item = {index: item for index, item in enumerate(items, start=1)}
    save_lock = Lock()
    completed = 0

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_to_index = {
            executor.submit(fetch_single_article, index, item, collection_config): index
            for index, item in enumerate(items, start=1)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                fetched_item = future.result()
            except Exception as exc:  # one bad page must not sink the whole batch
                fetched_item = failed_fetch_item(index, index_to_item[index], exc, collection_config)
                print(f"[{index}/{len(items)}] WARN unexpected fetch error: {exc}", file=sys.stderr)
            fetched_items[index] = fetched_item
            completed += 1

            if completed % CHECKPOINT_EVERY == 0:
                ordered_items = [fetched_items[i] for i in sorted(fetched_items)]
                with save_lock:
                    save_json_with_latest(ROOT_DIR, args.run_date, "raw_news.json", existing_items + ordered_items)

            safe_title = safe_text(fetched_item.get("title", ""))[:110]
            print(f"[{index}/{len(items)}] {fetched_item.get('fetch_status', 'unknown')}: {safe_title}")

    ordered_items = existing_items + [fetched_items[i] for i in sorted(fetched_items)]
    output_path = save_json_with_latest(ROOT_DIR, args.run_date, "raw_news.json", ordered_items)
    print(f"Loaded candidates from {input_path}")
    print(f"Saved {len(ordered_items)} fetched articles to {output_path}")


def load_config() -> dict[str, Any]:
    with (ROOT_DIR / "config.yaml").open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def existing_url_keys(articles: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for article in articles:
        keys.update(article_url_keys(article))
    return keys


def article_url_keys(article: dict[str, Any]) -> set[str]:
    return {
        url
        for url in [
            article.get("google_news_url"),
            article.get("resolved_url"),
            article.get("original_url"),
        ]
        if url
    }


def safe_text(value: str) -> str:
    return "".join(ch if ch.isprintable() or ch.isspace() else " " for ch in value)


def fetch_single_article(
    index: int, item: dict[str, Any], collection_config: dict[str, Any]
) -> dict[str, Any]:
    google_url = item.get("google_news_url", "")
    resolved = resolve_google_news_url(google_url)
    if resolved["resolve_status"] == "success":
        extracted = extract_article(
            resolved["resolved_url"],
            timeout=collection_config["request_timeout_seconds"],
            user_agent=collection_config["user_agent"],
        )
        fetch_status = extracted["fetch_status"]
        fetch_error = extracted["fetch_error"]
    else:
        extracted = {"original_url": "", "webpage_published_at": "", "article_text": ""}
        fetch_status = resolved["resolve_status"]
        fetch_error = resolved["resolve_error"]

    fetched_item = dict(item)
    fetched_item.update(
        {
            "_index": index,
            "resolved_url": resolved["resolved_url"],
            "resolve_status": resolved["resolve_status"],
            "resolve_error": resolved["resolve_error"],
            "original_url": extracted["original_url"],
            "webpage_published_at": extracted["webpage_published_at"],
            "article_text": extracted["article_text"],
            "fetch_status": fetch_status,
            "fetch_error": fetch_error,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    fetched_item.update(
        assess_freshness(
            fetched_item,
            max_age_days=collection_config.get("max_article_age_days", 2),
        )
    )
    return fetched_item


def failed_fetch_item(
    index: int, item: dict[str, Any], error: Exception, collection_config: dict[str, Any]
) -> dict[str, Any]:
    """Build a fetch record for an article whose worker raised an unexpected
    exception, so the rest of the batch is preserved instead of lost."""
    fetched_item = dict(item)
    fetched_item.update(
        {
            "_index": index,
            "resolved_url": "",
            "resolve_status": "error",
            "resolve_error": "",
            "original_url": "",
            "webpage_published_at": "",
            "article_text": "",
            "fetch_status": "fetch_error",
            "fetch_error": f"unexpected fetch error: {error}",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    fetched_item.update(
        assess_freshness(
            fetched_item,
            max_age_days=collection_config.get("max_article_age_days", 2),
        )
    )
    return fetched_item


if __name__ == "__main__":
    main()
