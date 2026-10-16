from __future__ import annotations

import argparse
import html
import json
import random
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import yaml

from pipeline_paths import add_run_date_arg, effective_run_date, run_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]
GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search"
GOOGLE_NEWS_TOPIC_URL = "https://news.google.com/rss/topics"
ENTITY_STOPWORDS = {
    "africa",
    "breaking",
    "daily",
    "federal",
    "government",
    "governor",
    "latest",
    "minister",
    "national",
    "news",
    "newspaper",
    "newspapers",
    "nigeria",
    "nigerian",
    "president",
    "state",
    "states",
}
IMPORTANT_ENTITY_WORDS = {
    "abuja",
    "afdb",
    "akk",
    "apc",
    "bayelsa",
    "benin",
    "bonga",
    "bpp",
    "buA".casefold(),
    "ccecc",
    "cccc",
    "cgc",
    "cng",
    "delta",
    "dangote",
    "disco",
    "ebid",
    "edo",
    "eib",
    "enugu",
    "fec",
    "fg",
    "fct",
    "ibadan",
    "ifc",
    "inec",
    "jica",
    "julius berger",
    "kaduna",
    "kano",
    "kogi",
    "lagos",
    "lekki",
    "lng",
    "nass",
    "ncdmb",
    "nerc",
    "ngic",
    "nlng",
    "nmdpra",
    "nnpc",
    "npa",
    "nrc",
    "nsia",
    "nupdc",
    "nuprc",
    "ogun",
    "ondo",
    "oyo",
    "pdp",
    "ppp",
    "rea",
    "rivers",
    "seplat",
    "tcn",
    "tinubu",
    "tetracore",
    "ukeF".casefold(),
    "world bank",
}
ENTITY_DUP_STRONG_TERMS = {
    "afdb",
    "akk",
    "bonga",
    "bpp",
    "bua",
    "ccecc",
    "cccc",
    "cgc",
    "cng",
    "dangote",
    "disco",
    "ebid",
    "eib",
    "fec",
    "ifc",
    "jica",
    "julius berger",
    "lng",
    "ncdmb",
    "nerc",
    "ngic",
    "nlng",
    "nmdpra",
    "nnpc",
    "npa",
    "nrc",
    "nsia",
    "nuprc",
    "ppp",
    "rea",
    "seplat",
    "tcn",
    "tetracore",
    "ukef",
    "world bank",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Google News RSS metadata.")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Maximum RSS items to keep per search query. Defaults to config collection.max_items_per_keyword.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Randomly sample this many discovered items after RSS dedupe. This is a total count, not per keyword.",
    )
    parser.add_argument("--sample-seed", type=int, default=None)
    parser.add_argument("--keywords", type=str, default=None)
    parser.add_argument(
        "--no-same-day-dedupe",
        action="store_true",
        help="Do not remove duplicate or near-duplicate RSS items discovered in the same run.",
    )
    parser.add_argument(
        "--same-day-threshold",
        type=float,
        default=0.60,
        help="Title similarity threshold for same-day RSS duplicate removal.",
    )
    parser.add_argument(
        "--no-history-dedupe",
        action="store_true",
        help="Do not remove RSS items already present in historical discovered_news.json files.",
    )
    parser.add_argument(
        "--history-days",
        type=int,
        default=30,
        help="Number of previous run days to compare against discovered_news.json history.",
    )
    parser.add_argument(
        "--history-threshold",
        type=float,
        default=0.60,
        help="Title similarity threshold for historical RSS duplicate removal.",
    )
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_date = effective_run_date(args.run_date)
    config = load_config()
    google_config = config["google_news"]
    collection_config = config["collection"]
    # feedparser uses urllib with no timeout by default; without this a single
    # hung Google News connection would stall the whole daily run indefinitely.
    socket.setdefaulttimeout(float(collection_config.get("request_timeout_seconds", 15)))
    search_specs = (
        build_cli_search_specs(parse_keywords(args.keywords), google_config)
        if args.keywords
        else build_search_specs(google_config, run_date)
    )
    max_items = args.max_items or collection_config["max_items_per_keyword"]

    discovered_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    feed_issues: list[dict[str, str]] = []
    total_rss_entries = 0

    print(f"Running {len(search_specs)} Google News search RSS queries")
    for spec in search_specs:
        keyword = spec["keyword"]
        query = build_query(google_config["base_query"], keyword, spec["lookback"])
        feed_url = build_feed_url(query, google_config["locale"])
        label = keyword or "<general>"
        parsed_feed, issues = parse_feed_safely(feed_url, f"{spec['group']}:{label}")
        feed_issues.extend(issues)
        if parsed_feed is None:
            print(f"[{spec['group']}:{label}] request failed, keeping 0")
            continue
        total_rss_entries += len(parsed_feed.entries)
        entries = parsed_feed.entries[:max_items]
        print(f"[{spec['group']}:{label}] found {len(parsed_feed.entries)} RSS items, keeping {len(entries)}")

        for entry in entries:
            google_url = entry.get("link")
            if not google_url or google_url in seen_urls:
                continue
            seen_urls.add(google_url)
            discovered_items.append(
                {
                    "title": entry.get("title", "").strip(),
                    "source": get_source(entry),
                    "published_at": normalize_published_at(entry),
                    "google_news_url": google_url,
                    "rss_summary": entry.get("summary", "").strip(),
                    "search_keyword": keyword,
                    "search_query": query,
                    "search_group": spec["group"],
                    "search_pool": spec["pool"],
                    "search_lookback": spec["lookback"],
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    for topic in google_config.get("topics", []):
        topic_name = str(topic.get("name") or topic.get("id") or "topic")
        topic_id = str(topic.get("id", "")).strip()
        if not topic_id:
            continue
        feed_url = build_topic_feed_url(topic_id, google_config["locale"])
        parsed_feed, issues = parse_feed_safely(feed_url, f"topic:{topic_name}")
        feed_issues.extend(issues)
        if parsed_feed is None:
            print(f"[topic:{topic_name}] request failed, keeping 0")
            continue
        total_rss_entries += len(parsed_feed.entries)
        entries = parsed_feed.entries[:max_items]
        print(f"[topic:{topic_name}] found {len(parsed_feed.entries)} RSS items, keeping {len(entries)}")

        for entry in entries:
            google_url = entry.get("link")
            if not google_url or google_url in seen_urls:
                continue
            seen_urls.add(google_url)
            discovered_items.append(
                {
                    "title": entry.get("title", "").strip(),
                    "source": get_source(entry),
                    "published_at": normalize_published_at(entry),
                    "google_news_url": google_url,
                    "rss_summary": entry.get("summary", "").strip(),
                    "search_keyword": f"topic:{topic_name}",
                    "search_query": f"topic:{topic_id}",
                    "search_group": "topic",
                    "search_pool": topic_name,
                    "search_lookback": "",
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    if total_rss_entries == 0 and (search_specs or google_config.get("topics")):
        print(
            "Discovery failed: all Google News RSS feeds returned 0 items. "
            "This usually means network access is blocked or Google News is unreachable.",
            file=sys.stderr,
        )
        if feed_issues:
            print("RSS request issues:", file=sys.stderr)
            for issue in feed_issues[:8]:
                print(f"- {format_feed_issue(issue)}", file=sys.stderr)
            if len(feed_issues) > 8:
                print(f"- ... {len(feed_issues) - 8} more issues", file=sys.stderr)
        else:
            print(
                "No parser exception was reported, but every configured feed was empty.",
                file=sys.stderr,
            )
        raise SystemExit(2)

    if feed_issues:
        print(f"Warning: {len(feed_issues)} RSS feeds reported request/parser issues")
        for issue in feed_issues[:5]:
            print(f"- {format_feed_issue(issue)}")

    print(f"Discovered {len(discovered_items)} unique Google News RSS URLs before title dedupe")

    same_day_duplicates: list[dict[str, Any]] = []
    if not args.no_same_day_dedupe:
        discovered_items, same_day_duplicates = remove_same_day_duplicates(
            discovered_items, args.same_day_threshold
        )
        duplicates_path = save_json_with_latest(
            ROOT_DIR, run_date, "discovery_same_day_duplicates.json", same_day_duplicates
        )
        print(f"Removed {len(same_day_duplicates)} same-day duplicate RSS items")
        print(f"Saved same-day duplicate report to {duplicates_path}")

    history_duplicates: list[dict[str, Any]] = []
    historical_items: list[dict[str, Any]] = []
    if not args.no_history_dedupe:
        historical_items = load_historical_discovered_items(run_date, args.history_days)
        if historical_items:
            discovered_items, history_duplicates = remove_historical_duplicates(
                discovered_items, historical_items, args.history_threshold
            )
            duplicates_path = save_json_with_latest(
                ROOT_DIR, run_date, "discovery_history_duplicates.json", history_duplicates
            )
            print(f"Compared against {len(historical_items)} RSS items from previous {args.history_days} days")
            print(f"Removed {len(history_duplicates)} historical duplicate RSS items")
            print(f"Saved historical duplicate report to {duplicates_path}")

    if args.sample_size is not None:
        before_sample_count = len(discovered_items)
        discovered_items = sample_discovered_items(
            discovered_items,
            args.sample_size,
            args.sample_seed if args.sample_seed is not None else run_date,
        )
        print(
            f"Sampled {len(discovered_items)} discovered items from {before_sample_count} "
            f"after RSS dedupe"
        )

    output_path = save_json_with_latest(
        ROOT_DIR, run_date, "discovered_news.json", discovered_items
    )
    print(f"Saved {len(discovered_items)} discovered items to {output_path}")


def load_historical_discovered_items(run_date: str, history_days: int) -> list[dict[str, Any]]:
    try:
        date_value = datetime.strptime(run_date, "%Y-%m-%d")
    except ValueError:
        return []

    historical_items: list[dict[str, Any]] = []
    for offset in range(1, max(0, history_days) + 1):
        candidate_date = (date_value - timedelta(days=offset)).strftime("%Y-%m-%d")
        candidate = run_path(ROOT_DIR, candidate_date, "discovered_news.json")
        if not candidate.exists():
            continue
        historical_items.extend(load_json(candidate))
    return historical_items


def parse_feed_safely(feed_url: str, label: str) -> tuple[Any | None, list[dict[str, str]]]:
    try:
        parsed_feed = feedparser.parse(feed_url)
    except Exception as exc:
        return None, [
            {
                "label": label,
                "url": feed_url,
                "status": "",
                "error": str(exc),
            }
        ]
    return parsed_feed, detect_feed_issues(parsed_feed, feed_url, label)


def detect_feed_issues(parsed_feed: Any, feed_url: str, label: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    status = parsed_feed.get("status")
    if status and int(status) >= 400:
        issues.append(
            {
                "label": label,
                "url": feed_url,
                "status": str(status),
                "error": f"HTTP {status}",
            }
        )

    if parsed_feed.get("bozo"):
        bozo_exception = parsed_feed.get("bozo_exception")
        issues.append(
            {
                "label": label,
                "url": feed_url,
                "status": str(status or ""),
                "error": str(bozo_exception or "feed parser reported malformed or unreachable feed"),
            }
        )
    return issues


def format_feed_issue(issue: dict[str, str]) -> str:
    status = f" status={issue['status']}" if issue.get("status") else ""
    error = " ".join(str(issue.get("error", "")).split())
    return f"{issue.get('label', 'feed')}{status}: {error}"


def sample_discovered_items(
    items: list[dict[str, Any]], sample_size: int, seed: int | str
) -> list[dict[str, Any]]:
    if sample_size <= 0 or sample_size >= len(items):
        return items
    rng = random.Random(seed)
    indexes = sorted(rng.sample(range(len(items)), sample_size))
    return [items[index] for index in indexes]


def remove_historical_duplicates(
    items: list[dict[str, Any]], historical_items: list[dict[str, Any]], threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    historical_urls = {
        normalize_url(item.get("google_news_url", "")) for item in historical_items if item.get("google_news_url")
    }
    historical_titles = [
        (
            index,
            item.get("title", ""),
            tokenize_title(item.get("title", "")),
            extract_entity_terms(item.get("title", "")),
        )
        for index, item in enumerate(historical_items, start=1)
    ]

    kept_items: list[dict[str, Any]] = []
    duplicate_items: list[dict[str, Any]] = []
    for item in items:
        url = normalize_url(item.get("google_news_url", ""))
        title = item.get("title", "")
        title_tokens = tokenize_title(title)
        entity_terms = extract_entity_terms(title)
        match: dict[str, Any] | None = None

        if url and url in historical_urls:
            match = {"match_type": "url", "score": 1.0, "historical_index": None, "historical_title": ""}
        else:
            best_score = 0.0
            best_shared_entities: set[str] = set()
            best_item: tuple[int, str] | None = None
            for historical_index, historical_title, historical_tokens, historical_entities in historical_titles:
                score = jaccard(title_tokens, historical_tokens)
                shared_entities = entity_terms & historical_entities
                if score > best_score:
                    best_score = score
                    best_shared_entities = shared_entities
                    best_item = (historical_index, historical_title)
                if len(shared_entities) > len(best_shared_entities):
                    best_shared_entities = shared_entities
                    best_item = (historical_index, historical_title)
            if best_item and best_score >= threshold:
                match = {
                    "match_type": "title",
                    "score": round(best_score, 3),
                    "entity_overlap": len(best_shared_entities),
                    "shared_entities": sorted(best_shared_entities),
                    "historical_index": best_item[0],
                    "historical_title": best_item[1],
                }
            elif best_item and is_entity_duplicate(best_shared_entities, best_score):
                match = {
                    "match_type": "entity",
                    "score": round(best_score, 3),
                    "entity_overlap": len(best_shared_entities),
                    "shared_entities": sorted(best_shared_entities),
                    "historical_index": best_item[0],
                    "historical_title": best_item[1],
                }

        if match:
            duplicate_item = dict(item)
            duplicate_item["history_duplicate"] = match
            duplicate_items.append(duplicate_item)
        else:
            kept_items.append(item)

    return kept_items, duplicate_items


def remove_same_day_duplicates(
    items: list[dict[str, Any]], threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept_items: list[dict[str, Any]] = []
    kept_titles: list[tuple[int, str, set[str], set[str]]] = []
    duplicate_items: list[dict[str, Any]] = []

    for item in items:
        title = item.get("title", "")
        title_tokens = tokenize_title(title)
        entity_terms = extract_entity_terms(title)
        title_key = normalize_title(title)
        best_score = 0.0
        best_shared_entities: set[str] = set()
        best_item: tuple[int, str] | None = None
        exact_match = False

        for kept_index, kept_title, kept_tokens, kept_entities in kept_titles:
            if title_key and title_key == normalize_title(kept_title):
                best_score = 1.0
                best_shared_entities = entity_terms & kept_entities
                best_item = (kept_index, kept_title)
                exact_match = True
                break
            score = jaccard(title_tokens, kept_tokens)
            shared_entities = entity_terms & kept_entities
            if score > best_score:
                best_score = score
                best_shared_entities = shared_entities
                best_item = (kept_index, kept_title)
            if len(shared_entities) > len(best_shared_entities):
                best_shared_entities = shared_entities
                best_item = (kept_index, kept_title)

        if best_item and (
            exact_match
            or best_score >= threshold
            or is_entity_duplicate(best_shared_entities, best_score)
        ):
            duplicate_item = dict(item)
            duplicate_item["same_day_duplicate"] = {
                "match_type": duplicate_match_type(exact_match, best_score, threshold),
                "score": round(best_score, 3),
                "entity_overlap": len(best_shared_entities),
                "shared_entities": sorted(best_shared_entities),
                "kept_index": best_item[0],
                "kept_title": best_item[1],
            }
            duplicate_items.append(duplicate_item)
            continue

        kept_items.append(item)
        kept_titles.append((len(kept_items), title, title_tokens, entity_terms))

    return kept_items, duplicate_items


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


def normalize_title(title: str) -> str:
    text = html.unescape(str(title or "")).lower()
    text = re.sub(r"\s+-\s+[^-]+$", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def extract_entity_terms(title: str) -> set[str]:
    text = html.unescape(str(title or ""))
    without_source = re.sub(r"\s+-\s+[^-]+$", "", text).strip()
    terms: set[str] = set()

    amount_patterns = [
        r"(?:\$|₦|N)\s?\d[\d,.]*(?:\s?(?:bn|billion|m|million|trillion))?",
        r"\d[\d,.]*\s?(?:bn|billion|m|million|trillion)\s?(?:naira|dollars|euros|pounds)?",
        r"\d[\d,.]*\s?(?:MW|GW|MWh|km|kilometres|kilometers|hectares|bpd|tons|tonnes)",
    ]
    for pattern in amount_patterns:
        for match in re.finditer(pattern, without_source, flags=re.IGNORECASE):
            terms.add(normalize_entity(match.group(0)))

    for phrase in re.findall(r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4}\b", without_source):
        normalized = normalize_entity(phrase)
        if is_useful_entity(normalized):
            terms.add(normalized)

    acronyms = re.findall(r"\b[A-Z]{2,}(?:\d+)?\b", without_source)
    for acronym in acronyms:
        normalized = normalize_entity(acronym)
        if is_useful_entity(normalized):
            terms.add(normalized)

    for word in re.findall(r"\b[A-Za-z][A-Za-z'-]{3,}\b", without_source):
        normalized = normalize_entity(word)
        if normalized in IMPORTANT_ENTITY_WORDS:
            terms.add(normalized)

    return terms


def normalize_entity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def is_useful_entity(entity: str) -> bool:
    if not entity or entity in ENTITY_STOPWORDS:
        return False
    if entity.startswith("nigeria ") or entity == "nigeria":
        return False
    return len(entity) >= 3


def is_entity_duplicate(shared_entities: set[str], title_score: float) -> bool:
    if len(shared_entities) >= 3 and title_score >= 0.25:
        return True
    if len(shared_entities) >= 2 and title_score >= 0.15 and has_strong_entity(shared_entities):
        return True
    return False


def has_strong_entity(entity_terms: set[str]) -> bool:
    return any(
        re.search(r"\d", entity)
        or entity in ENTITY_DUP_STRONG_TERMS
        for entity in entity_terms
    )


def duplicate_match_type(exact_match: bool, score: float, threshold: float) -> str:
    if exact_match:
        return "exact_title"
    if score >= threshold:
        return "similar_title"
    return "entity"


def normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def load_config() -> dict[str, Any]:
    with (ROOT_DIR / "config.yaml").open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def parse_keywords(raw_keywords: str | None) -> list[str] | None:
    if not raw_keywords:
        return None
    return [keyword.strip() for keyword in raw_keywords.split(",") if keyword.strip()]


def build_cli_search_specs(
    keywords: list[str] | None, google_config: dict[str, Any]
) -> list[dict[str, str]]:
    lookback = google_config.get("core", {}).get("lookback") or google_config.get("lookback", "1d")
    return [
        {"keyword": keyword, "lookback": lookback, "group": "cli", "pool": "cli"}
        for keyword in (keywords or [])
    ]


def build_search_specs(google_config: dict[str, Any], run_date: str) -> list[dict[str, str]]:
    if "core" not in google_config:
        return [
            {
                "keyword": keyword,
                "lookback": google_config.get("lookback", "1d"),
                "group": "legacy",
                "pool": "keywords",
            }
            for keyword in build_keywords(google_config)
        ]

    specs: list[dict[str, str]] = []
    core_config = google_config.get("core", {})
    for keyword in core_config.get("keywords", []):
        specs.append(
            {
                "keyword": str(keyword),
                "lookback": core_config.get("lookback", "1d"),
                "group": "core",
                "pool": "core",
            }
        )

    rotation_config = google_config.get("rotation", {})
    if rotation_config.get("enabled", False):
        weekday = parse_run_date(run_date).strftime("%A").casefold()
        pool_names = rotation_config.get("schedule", {}).get(weekday, [])
        pools = rotation_config.get("pools", {})
        default_lookback = rotation_config.get("default_lookback", "7d")
        # daily_limit caps keywords PER scheduled pool. It used to be a single
        # global counter across all pools, which meant that once the first pool
        # filled the limit, every later pool scheduled that day was silently
        # skipped (e.g. Monday's multilateral_finance pool never ran).
        daily_limit = int(rotation_config.get("daily_limit", 0) or 0)
        for pool_name in pool_names:
            pool_config = pools.get(pool_name, {})
            lookback = pool_config.get("lookback", default_lookback)
            pool_count = 0
            for keyword in pool_config.get("keywords", []):
                if daily_limit and pool_count >= daily_limit:
                    break
                specs.append(
                    {
                        "keyword": str(keyword),
                        "lookback": lookback,
                        "group": "rotation",
                        "pool": str(pool_name),
                    }
                )
                pool_count += 1

    return dedupe_search_specs(specs)


def dedupe_search_specs(specs: list[dict[str, str]]) -> list[dict[str, str]]:
    unique_specs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        key = (spec["keyword"].casefold().strip(), spec["lookback"])
        if key in seen:
            continue
        seen.add(key)
        unique_specs.append(spec)
    return unique_specs


def parse_run_date(run_date: str) -> datetime:
    try:
        return datetime.strptime(run_date, "%Y-%m-%d")
    except ValueError:
        return datetime.now()


def build_keywords(google_config: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    if google_config.get("include_general", True):
        keywords.append("")
    keywords.extend(google_config["keywords"])
    return keywords


def build_query(base_query: str, keyword: str, lookback: str) -> str:
    parts = [base_query]
    if keyword:
        parts.append(str(keyword))
    parts.append(f"when:{lookback}")
    return " ".join(parts)


def build_feed_url(query: str, locale: dict[str, str]) -> str:
    encoded_query = quote_plus(query)
    return (
        f"{GOOGLE_NEWS_SEARCH_URL}?q={encoded_query}"
        f"&hl={locale['hl']}&gl={locale['gl']}&ceid={locale['ceid']}"
    )


def build_topic_feed_url(topic_id: str, locale: dict[str, str]) -> str:
    return (
        f"{GOOGLE_NEWS_TOPIC_URL}/{topic_id}"
        f"?hl={locale['hl']}&gl={locale['gl']}&ceid={locale['ceid']}"
    )


def get_source(entry: Any) -> str:
    source = entry.get("source")
    if isinstance(source, dict):
        return source.get("title", "").strip()
    return ""


def normalize_published_at(entry: Any) -> str:
    published = entry.get("published")
    if not published:
        return ""

    try:
        return parsedate_to_datetime(published).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return published


if __name__ == "__main__":
    main()
