from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


URL_DATE_PATTERN = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)")
# West Africa Time (UTC+1, no DST). Use this rather than date.today() so "today"
# is the Nigerian editorial day regardless of where the machine clock is set.
NIGERIA_TZ = timezone(timedelta(hours=1))


def assess_freshness(article: dict[str, Any], max_age_days: int = 2) -> dict[str, Any]:
    article_date = extract_article_date(article)
    if article_date is None:
        return {
            "freshness_status": "unknown",
            "freshness_reason": "No webpage or URL date found.",
            "freshness_source": "",
            "article_date": "",
            "article_age_days": None,
        }

    article_date_value, source = article_date
    today = datetime.now(NIGERIA_TZ).date()
    age_days = (today - article_date_value).days
    if age_days > max_age_days:
        return {
            "freshness_status": "stale",
            "freshness_reason": f"{source} date is {age_days} days old.",
            "freshness_source": source,
            "article_date": article_date_value.isoformat(),
            "article_age_days": age_days,
        }

    return {
        "freshness_status": "fresh",
        "freshness_reason": f"{source} date is {age_days} days old.",
        "freshness_source": source,
        "article_date": article_date_value.isoformat(),
        "article_age_days": age_days,
    }


def extract_article_date(article: dict[str, Any]) -> tuple[date, str] | None:
    webpage_date = parse_date_value(
        article.get("webpage_published_at", "")
        or article.get("article_published_at", "")
    )
    if webpage_date is not None:
        return webpage_date, "webpage"

    url_date = extract_url_date(
        article.get("resolved_url", "")
        or article.get("original_url", "")
        or article.get("google_news_url", "")
    )
    if url_date is not None:
        return url_date, "URL"
    return None


def parse_date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    candidates = [
        text,
        text.replace("Z", "+00:00"),
        text[:10],
    ]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            pass

    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_url_date(url: str) -> date | None:
    match = URL_DATE_PATTERN.search(url)
    if not match:
        return None

    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
