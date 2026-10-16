from __future__ import annotations

import json
import argparse
import html
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from filter import is_reviewable, mark_minimum_backfill, selection_sort_key
from pipeline_paths import (
    add_run_date_arg,
    effective_run_date,
    run_path,
    save_json_with_latest,
    write_text_with_latest,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT_DIR / "config" / "scoring_rules.yaml"

LEVEL_ORDER = ["high", "medium", "low", "below_threshold"]
FETCH_FAILED_STATUSES = {"request_failed", "extract_failed", "resolve_failed"}
CATEGORY_BY_NEWS_TYPE = {
    "project_opportunity": "基础设施/项目动态",
    "financing_budget": "融资、财政与预算",
    "policy_regulation": "政策与监管",
    "election_politics": "政治动态",
    "security_risk": "安全动态",
    "market_logistics": "交通、港口与物流",
    "industrial_investment": "工业、制造与园区",
    "energy_oil_gas": "油气、能源",
    "power_renewables": "电力与新能源",
    "agriculture_value_chain": "农业与产业链",
    "multilateral_bilateral": "双边/多边合作",
    "industry_report": "行业趋势研报",
    "irrelevant": "其他",
}


def load_articles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Markdown review file.")
    parser.add_argument(
        "--previous-review",
        default=None,
        help="Review markdown to use for cross-day duplicate removal. Defaults to yesterday when available.",
    )
    parser.add_argument(
        "--no-cross-day-dedupe",
        action="store_true",
        help="Do not remove items already present in a previous review file.",
    )
    parser.add_argument(
        "--cross-day-threshold",
        type=float,
        default=0.45,
        help="Title similarity threshold for cross-day duplicate removal.",
    )
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_date = effective_run_date(args.run_date)
    deduped_path = run_path(ROOT_DIR, run_date, "deduped_news.json")
    scored_path = run_path(ROOT_DIR, run_date, "scored_news.json")
    input_path = deduped_path if deduped_path.exists() else scored_path
    if not input_path.exists():
        raise FileNotFoundError(
            f"Expected deduped_news.json or scored_news.json in run directory: {input_path.parent}"
        )
    articles = load_articles(input_path)
    rules = load_rules()
    duplicates: list[dict[str, Any]] = []
    previous_review_path = None
    if not args.no_cross_day_dedupe:
        previous_review_path = find_previous_review(run_date, args.previous_review)
        if previous_review_path:
            articles, duplicates = mark_previous_review_duplicates(
                articles, previous_review_path, args.cross_day_threshold
            )
            save_json_with_latest(
                ROOT_DIR, run_date, "cross_day_duplicates.json", duplicates
            )

    scored_articles = load_articles(scored_path)
    articles, minimum_backfills = enforce_final_minimums(
        articles,
        scored_articles,
        rules,
        previous_review_path,
        args.cross_day_threshold,
    )
    if minimum_backfills:
        save_json_with_latest(ROOT_DIR, run_date, "review_minimum_backfills.json", minimum_backfills)

    grouped_articles = group_articles(articles)
    review_urls = {canonical_url(article_url(article)) for article in articles if article_url(article)}
    fetch_issues = [
        issue
        for issue in load_fetch_issues(run_path(ROOT_DIR, run_date, "raw_news.json"))
        if canonical_url(article_url(issue)) not in review_urls
    ]
    markdown = render_review(grouped_articles, len(articles), fetch_issues)

    review_path = write_text_with_latest(ROOT_DIR, run_date, "review.md", markdown)

    print(f"Loaded {len(articles)} articles from {input_path}")
    if previous_review_path:
        print(f"Compared against previous review {previous_review_path}")
        print(f"Marked {len(duplicates)} cross-day possible duplicate articles")
    if minimum_backfills:
        print(f"Added {len(minimum_backfills)} final-review minimum backfill articles")
    print(f"Saved review to {review_path}")


def load_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def find_previous_review(run_date: str, explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path if path.exists() else None

    try:
        date_value = datetime.strptime(run_date, "%Y-%m-%d")
    except ValueError:
        return None

    for offset in range(1, 8):
        candidate_date = (date_value - timedelta(days=offset)).strftime("%Y-%m-%d")
        candidate = run_path(ROOT_DIR, candidate_date, "review.md")
        if candidate.exists():
            return candidate
    return None


def mark_previous_review_duplicates(
    articles: list[dict[str, Any]], previous_review_path: Path, threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    previous_items = parse_review_items(previous_review_path)
    previous_urls = {item["url"] for item in previous_items if item["url"]}
    previous_titles = [
        (item["number"], item["title"], tokenize_title(item["title"])) for item in previous_items
    ]

    marked_articles = []
    duplicate_articles = []
    for article in articles:
        url = canonical_url(article_url(article))
        title = article.get("title", "")
        title_tokens = tokenize_title(title)
        match = None

        if url and url in previous_urls:
            match = {"match_type": "url", "score": 1.0, "previous_title": "", "previous_number": None}
        else:
            best_score = 0.0
            best_item: tuple[int, str] | None = None
            for previous_number, previous_title, previous_tokens in previous_titles:
                score = jaccard(title_tokens, previous_tokens)
                if score > best_score:
                    best_score = score
                    best_item = (previous_number, previous_title)
            if best_item and best_score >= threshold:
                match = {
                    "match_type": "title",
                    "score": round(best_score, 3),
                    "previous_number": best_item[0],
                    "previous_title": best_item[1],
                }

        if match:
            duplicate = dict(article)
            duplicate["cross_day_duplicate"] = match
            duplicate["previous_review_path"] = str(previous_review_path)
            duplicate_articles.append(duplicate)
            marked_articles.append(duplicate)
        else:
            marked_articles.append(article)

    return marked_articles, duplicate_articles


def enforce_final_minimums(
    articles: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    rules: dict[str, Any],
    previous_review_path: Path | None,
    cross_day_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    minimums = rules.get("selection_minimums", {})
    if not minimums:
        return articles, []

    limits = rules.get("selection_limits", {})
    default_limit = int(limits.get("default", 4))
    selected_articles = list(articles)
    selected_keys = {article_key(article) for article in selected_articles}
    previous_items = parse_review_items(previous_review_path) if previous_review_path else []
    backfills: list[dict[str, Any]] = []

    for lane, minimum in minimums.items():
        minimum_count = int(minimum)
        current_count = sum(
            1 for article in selected_articles if article.get("selection_lane", "other") == lane
        )
        if current_count >= minimum_count:
            continue

        lane_limit = int(limits.get(lane, default_limit))
        if current_count >= lane_limit:
            continue

        lane_candidates = [
            article
            for article in candidates
            if article.get("selection_lane", "other") == lane
            and article_key(article) not in selected_keys
            and is_reviewable(article)
            and not matches_previous_review(article, previous_items, cross_day_threshold)
        ]
        lane_candidates.sort(key=selection_sort_key, reverse=True)

        for candidate in lane_candidates:
            if is_same_day_duplicate(candidate, selected_articles):
                continue
            backfilled = mark_minimum_backfill(candidate, lane)
            reasons = list(backfilled.get("relevance_reasons", []))
            reasons.append("final review minimum backfill after dedupe/cross-day dedupe")
            backfilled["relevance_reasons"] = reasons
            backfilled["final_review_minimum_backfill"] = True
            selected_articles.append(backfilled)
            selected_keys.add(article_key(backfilled))
            backfills.append(backfilled)
            current_count += 1
            if current_count >= minimum_count or current_count >= lane_limit:
                break

    selected_articles.sort(key=selection_sort_key, reverse=True)
    return selected_articles, backfills


def matches_previous_review(
    article: dict[str, Any],
    previous_items: list[dict[str, Any]],
    threshold: float,
) -> bool:
    if not previous_items:
        return False
    url = canonical_url(article_url(article))
    title_tokens = tokenize_title(article.get("title", ""))
    for item in previous_items:
        if url and url == item.get("url"):
            return True
        if jaccard(title_tokens, tokenize_title(item.get("title", ""))) >= threshold:
            return True
    return False


def is_same_day_duplicate(article: dict[str, Any], selected_articles: list[dict[str, Any]]) -> bool:
    url = canonical_url(article_url(article))
    title_tokens = tokenize_title(article.get("title", ""))
    for selected in selected_articles:
        selected_url = canonical_url(article_url(selected))
        if url and selected_url and url == selected_url:
            return True
        if article.get("selection_lane") == selected.get("selection_lane") and jaccard(
            title_tokens, tokenize_title(selected.get("title", ""))
        ) >= 0.65:
            return True
    return False


def article_key(article: dict[str, Any]) -> str:
    return canonical_url(article_url(article)) or str(article.get("title") or id(article)).casefold()


def parse_review_items(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^### (\d+)\. (.*?)\n(?P<body>.*?)(?=^### \d+\. |\Z)", re.M | re.S)
    items = []
    for match in pattern.finditer(text):
        body = match.group("body")
        url_match = re.search(r"^- URL: (.*)$", body, re.M)
        items.append(
            {
                "number": int(match.group(1)),
                "title": match.group(2).strip(),
                "url": canonical_url(url_match.group(1).strip() if url_match else ""),
            }
        )
    return items


def article_url(article: dict[str, Any]) -> str:
    return (
        article.get("resolved_url")
        or article.get("original_url")
        or article.get("google_news_url")
        or ""
    )


def canonical_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def tokenize_title(title: str) -> set[str]:
    text = html.unescape(str(title or "")).lower()
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "are",
        "was",
        "has",
        "have",
        "into",
        "over",
        "nigeria",
        "nigerian",
        "news",
        "newspaper",
        "newspapers",
    }
    return {word for word in words if len(word) > 2 and word not in stopwords}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def group_articles(articles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        if article.get("manual_include"):
            grouped_articles["manual_include"].append(article)
        elif is_fetch_failed(article):
            grouped_articles["fetch_failed"].append(article)
        elif article.get("cross_day_duplicate"):
            grouped_articles["cross_day_duplicate"].append(article)
        else:
            grouped_articles[normalize_relevance_level(article.get("relevance_level", "below_threshold"))].append(article)

    for level_articles in grouped_articles.values():
        level_articles.sort(key=lambda article: article.get("relevance_score", 0), reverse=True)

    return grouped_articles


def is_fetch_failed(article: dict[str, Any]) -> bool:
    return str(article.get("fetch_status") or "") in FETCH_FAILED_STATUSES


def normalize_relevance_level(level: Any) -> str:
    normalized = str(level or "below_threshold")
    if normalized == "ignored":
        return "below_threshold"
    return normalized


def load_fetch_issues(path: Path) -> list[dict[str, Any]]:
    return [
        article
        for article in load_articles(path)
        if article.get("fetch_status") not in {"", "success", None}
    ]


def render_review(
    grouped_articles: dict[str, list[dict[str, Any]]],
    total: int,
    fetch_issues: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "# Nigeria News Review",
        "",
        f"Total articles: {total}",
        "",
    ]

    manual_articles = grouped_articles.get("manual_include", [])
    if manual_articles:
        lines.extend([f"## Manual Includes ({len(manual_articles)})", ""])
        for index, article in enumerate(manual_articles, start=1):
            lines.extend(render_article(index, article))
            lines.append("")

    for level in LEVEL_ORDER:
        articles = grouped_articles.get(level, [])
        lines.extend([f"## {section_title(level)} ({len(articles)})", ""])
        for index, article in enumerate(articles, start=1):
            lines.extend(render_article(index, article))
            lines.append("")

    cross_day_articles = grouped_articles.get("cross_day_duplicate", [])
    if cross_day_articles:
        lines.extend([f"## Cross-Day Possible Duplicates ({len(cross_day_articles)})", ""])
        for index, article in enumerate(cross_day_articles, start=1):
            lines.extend(render_article(index, article))
            lines.append("")

    fetch_failed_articles = grouped_articles.get("fetch_failed", [])
    if fetch_failed_articles:
        lines.extend([f"## Fetch Failed ({len(fetch_failed_articles)})", ""])
        for index, article in enumerate(fetch_failed_articles, start=1):
            lines.extend(render_article(index, article, reviewable=False))
            lines.append("")

    if fetch_issues:
        lines.extend(render_fetch_issues(fetch_issues))

    return "\n".join(lines).rstrip() + "\n"


def section_title(level: str) -> str:
    if level == "below_threshold":
        return "Below Threshold"
    return level.title()


def render_article(index: int, article: dict[str, Any], reviewable: bool = True) -> list[str]:
    title = article.get("title", "Untitled")
    url = article.get("resolved_url") or article.get("original_url") or article.get("google_news_url", "")
    score = article.get("relevance_score", 0)
    source = article.get("source", "")
    news_type = article.get("news_type", "")
    project_stage = article.get("project_stage", "")
    fetch_status = article.get("fetch_status", "")

    lines = [
        f"### {index}. {title}",
        "",
    ]
    if reviewable:
        review_decision = (
            "[x] include [ ] maybe [ ] exclude"
            if article.get("manual_include")
            else "[ ] include [ ] maybe [ ] exclude"
        )
        final_category = suggested_category(article) if article.get("manual_include") else ""
        final_title = suggested_title(article) if article.get("manual_include") else ""
        lines.extend(
            [
                f"- Review decision: {review_decision}",
                f"- Suggested category: {suggested_category(article)}",
                f"- Suggested title: {suggested_title(article)}",
                f"- Final category: {final_category}",
                f"- Final title: {final_title}",
                f"- Notes: {article.get('manual_include_reason', '') if article.get('manual_include') else ''}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- Review action: manual link check only; no article text is available for LLM input.",
                "",
            ]
        )

    lines.extend(
        [
            f"- Source: {source}",
            f"- Score: {score}",
            f"- Type: {news_type}",
            f"- Stage: {project_stage}",
            f"- Fetch: {fetch_status}",
            f"- URL: {url}",
            f"- Watch reason: {article.get('watch_reason', '')}",
        ]
    )

    duplicate_count = article.get("duplicate_count", 0)
    if duplicate_count:
        lines.append(f"- Duplicates collapsed: {duplicate_count}")
        duplicate_sources = article.get("duplicate_sources", [])
        if duplicate_sources:
            lines.append(f"- Duplicate sources: {', '.join(duplicate_sources)}")

    cross_day_duplicate = article.get("cross_day_duplicate")
    if cross_day_duplicate:
        lines.append(
            "- Cross-day possible duplicate: "
            f"{cross_day_duplicate.get('match_type', '')} "
            f"score={cross_day_duplicate.get('score', '')} "
            f"previous=#{cross_day_duplicate.get('previous_number', '')} "
            f"{cross_day_duplicate.get('previous_title', '')}".strip()
        )

    matched_keywords = article.get("matched_keywords", [])
    if matched_keywords:
        lines.append(f"- Matched keywords: {', '.join(matched_keywords[:30])}")

    reasons = article.get("relevance_reasons", [])
    if reasons:
        lines.append("- Scoring reasons:")
        for reason in reasons[:8]:
            lines.append(f"  - {reason}")

    evidence_snippets = article.get("evidence_snippets", [])
    if evidence_snippets:
        lines.append("- Evidence snippets:")
        for snippet in evidence_snippets[:4]:
            lines.append(f"  - {snippet}")

    excerpt = clean_excerpt(article.get("article_text", ""))
    if excerpt:
        lines.extend(["", excerpt])

    return lines


def render_fetch_issues(articles: list[dict[str, Any]]) -> list[str]:
    lines = ["## Fetch Issues ({})".format(len(articles)), ""]
    for index, article in enumerate(articles, start=1):
        title = article.get("title", "Untitled")
        status = article.get("fetch_status", "")
        url = article_url(article)
        google_url = article.get("google_news_url", "")
        error = article.get("fetch_error") or article.get("resolve_error") or ""
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"- Fetch: {status}",
                f"- URL: {url}",
                f"- Google News URL: {google_url}",
                f"- Error: {short_error(error)}",
                "",
            ]
        )
    return lines


def short_error(error: str, max_length: int = 500) -> str:
    compact_error = " ".join(str(error or "").split())
    if len(compact_error) <= max_length:
        return compact_error
    return compact_error[:max_length].rstrip() + "..."


def suggested_category(article: dict[str, Any]) -> str:
    news_type = article.get("news_type", "irrelevant")
    return CATEGORY_BY_NEWS_TYPE.get(news_type, "其他")


def suggested_title(article: dict[str, Any]) -> str:
    return article.get("title", "Untitled")


def clean_excerpt(text: str, max_length: int = 700) -> str:
    compact_text = " ".join(text.split())
    if not compact_text:
        return ""

    if len(compact_text) <= max_length:
        return f"> {compact_text}"

    return f"> {compact_text[:max_length].rstrip()}..."


if __name__ == "__main__":
    main()
