from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from extractors.article import extract_article
from extractors.google_news_url import resolve_google_news_url
from pipeline_paths import effective_run_date, latest_path, run_path
from processors.freshness import assess_freshness
from processors.relevance import score_article


ROOT_DIR = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT_DIR / "config" / "scoring_rules.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manually add a fetched article to today's review.")
    parser.add_argument("--run-date", default=None)
    parser.add_argument("--title", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--category", default="政治动态与政府治理")
    parser.add_argument("--notes", default="Manually added after review; election primary coverage.")
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--decision", choices=["include", "maybe", "exclude", "unreviewed"], default="include")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_date = effective_run_date(args.run_date)
    config = load_config()
    user_agent = args.user_agent or config["collection"]["user_agent"]
    rules = load_rules()

    fetch_url = args.url
    google_news_url = ""
    if "news.google.com/rss/articles/" in args.url:
        google_news_url = args.url
        resolved = resolve_google_news_url(args.url)
        if resolved["resolve_status"] == "success" and resolved["resolved_url"]:
            fetch_url = resolved["resolved_url"]
            print(f"Resolved Google News URL -> {fetch_url}")
        else:
            print(f"Warning: failed to resolve Google News URL: {resolved.get('resolve_error','')}")

    fetched = extract_article(fetch_url, args.timeout, user_agent)
    article = {
        "title": args.title,
        "source": args.source,
        "published_at": "",
        "google_news_url": google_news_url,
        "rss_summary": "",
        "search_keyword": "manual election primary",
        "search_query": "manual election primary",
        "search_group": "manual",
        "search_pool": "manual",
        "search_lookback": "",
        "discovered_at": now_iso(),
        "llm_prefilter_decision": "fetch",
        "llm_prefilter_priority": "high",
        "llm_prefilter_news_type": args.category,
        "llm_prefilter_reason": args.notes,
        "_index": manual_index(run_date),
        "resolved_url": fetch_url,
        "resolve_status": "manual",
        "resolve_error": "",
        **fetched,
        "collected_at": now_iso(),
        "manual_include": True,
        "manual_include_reason": args.notes,
    }
    article.update(assess_freshness(article, max_age_days=config["collection"].get("max_article_age_days", 3)))
    scored = score_article(article, rules)
    scored.update(
        {
            "manual_include": True,
            "manual_include_reason": args.notes,
            "review_candidate_status": "candidate",
            "relevance_level": scored.get("relevance_level") or "high",
        }
    )

    for filename in ["raw_news.json", "scored_news.json", "review_candidates.json", "selected_news.json", "deduped_news.json"]:
        path = run_path(ROOT_DIR, run_date, filename)
        items = load_json(path)
        item = article if filename == "raw_news.json" else scored
        if not has_article(items, args.url, args.title):
            items.append(item)
        else:
            items = [item if is_article(existing, args.url, args.title) else existing for existing in items]
        save_json_and_sync(run_date, filename, items)

    append_review(run_date, scored, args.category, args.decision, args.notes)

    print(f"Added manual article to run {run_date}: {args.title}")
    print(f"Fetch status: {scored.get('fetch_status')}")
    print(f"Review path: {run_path(ROOT_DIR, run_date, 'review.md')}")


def load_config() -> dict[str, Any]:
    with (ROOT_DIR / "config.yaml").open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json_and_sync(run_date: str, filename: str, items: list[dict[str, Any]]) -> None:
    path = run_path(ROOT_DIR, run_date, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(path, latest_path(ROOT_DIR, filename))


def article_url(article: dict[str, Any]) -> str:
    return article.get("resolved_url") or article.get("original_url") or article.get("google_news_url") or ""


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().rstrip("/").casefold())


def is_article(article: dict[str, Any], url: str, title: str) -> bool:
    return normalize(article_url(article)) == normalize(url) or normalize(article.get("title", "")) == normalize(title)


def has_article(items: list[dict[str, Any]], url: str, title: str) -> bool:
    return any(is_article(item, url, title) for item in items)


def manual_index(run_date: str) -> int:
    items = load_json(run_path(ROOT_DIR, run_date, "raw_news.json"))
    return max([int(item.get("_index", 0) or 0) for item in items] + [0]) + 1


def append_review(
    run_date: str,
    item: dict[str, Any],
    category: str,
    decision: str,
    notes: str,
) -> None:
    review_path = run_path(ROOT_DIR, run_date, "review.md")
    text = review_path.read_text(encoding="utf-8") if review_path.exists() else "# Nigeria News Review\n\nTotal articles: 0\n"
    text = remove_existing_review_entry(text, item)
    current_total = count_review_articles(text)
    text = re.sub(r"Total articles: \d+", f"Total articles: {current_total + 1}", text, count=1)
    section = "\n".join(render_review_article(current_total + 1, item, category, decision, notes))
    updated = text.rstrip() + "\n\n## Manual Includes Added After Review\n\n" + section + "\n"
    review_path.write_text(updated, encoding="utf-8")
    shutil.copyfile(review_path, latest_path(ROOT_DIR, "review.md"))


def remove_existing_review_entry(text: str, item: dict[str, Any]) -> str:
    target_url = normalize(article_url(item))
    if not target_url:
        return text
    pattern = re.compile(r"(?ms)^###\s+\d+\.\s+.*?(?=^###\s+\d+\.|^##\s+|\Z)")
    last_end = 0
    output_parts: list[str] = []

    for match in pattern.finditer(text):
        output_parts.append(text[last_end : match.start()])
        block = match.group(0)
        url_match = re.search(r"(?m)^- URL:\s*(.*?)\s*$", block)
        block_url = normalize(url_match.group(1)) if url_match else ""
        if block_url != target_url:
            output_parts.append(block)
        last_end = match.end()

    output_parts.append(text[last_end:])
    return "".join(output_parts).rstrip() + "\n"


def count_review_articles(text: str) -> int:
    reviewable_text = re.split(
        r"(?m)^##\s+(Below Threshold|Fetch Failed|Fetch Issues|Cross-Day Possible Duplicates)\b",
        text,
        maxsplit=1,
    )[0]
    return len(re.findall(r"(?m)^###\s+\d+\.\s+", reviewable_text))


def render_review_article(
    index: int,
    item: dict[str, Any],
    category: str,
    decision: str,
    notes: str,
) -> list[str]:
    title = item.get("title", "Untitled")
    checkbox = {
        "include": "[x] include [ ] maybe [ ] exclude",
        "maybe": "[ ] include [x] maybe [ ] exclude",
        "exclude": "[ ] include [ ] maybe [x] exclude",
        "unreviewed": "[ ] include [ ] maybe [ ] exclude",
    }[decision]
    article_text = " ".join(str(item.get("article_text", "")).split())
    excerpt = article_text[:900].rstrip()
    if len(article_text) > 900:
        excerpt += "..."

    lines = [
        f"### {index}. {title}",
        "",
        f"- Review decision: {checkbox}",
        f"- Suggested category: {category}",
        f"- Suggested title: {title}",
        f"- Final category: {category}",
        f"- Final title: {title}",
        f"- Notes: {notes}",
        "",
        f"- Source: {item.get('source', '')}",
        f"- Score: {item.get('relevance_score', 0)}",
        f"- Type: {item.get('news_type', '')}",
        f"- Stage: {item.get('project_stage', '')}",
        f"- Fetch: {item.get('fetch_status', '')}",
        f"- URL: {article_url(item)}",
        f"- Watch reason: {item.get('watch_reason', '')}",
    ]
    if item.get("matched_keywords"):
        lines.append(f"- Matched keywords: {', '.join(item['matched_keywords'])}")
    if item.get("relevance_reasons"):
        lines.extend(["- Scoring reasons:"] + [f"  - {reason}" for reason in item["relevance_reasons"]])
    if item.get("evidence_snippets"):
        lines.extend(["- Evidence snippets:"] + [f"  - {snippet}" for snippet in item["evidence_snippets"][:4]])
    lines.extend(["", f"> {excerpt}"])
    return lines


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
