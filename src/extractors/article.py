from __future__ import annotations

import random
import time
from typing import Any

import requests
import trafilatura
from lxml import html as lxml_html

from processors.text_cleaning import clean_article_text

try:  # readability-lxml is an optional but recommended fallback extractor
    from readability import Document as _ReadabilityDocument
except Exception:  # pragma: no cover - degrade gracefully if not installed
    _ReadabilityDocument = None


# Anything shorter than this (cleaned English body chars) is almost certainly a
# paywall stub, consent/cookie wall, or JS-only page — not a real article body.
MIN_ARTICLE_TEXT_CHARS = 120

# Network resilience knobs. Retries only cost time on the (few) failing items.
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.6
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _browser_headers(user_agent: str) -> dict[str, str]:
    # A fuller, browser-like header set gets past a lot of naive bot blocks that
    # reject a bare User-Agent with a 403.
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _get(url: str, timeout: int, headers: dict[str, str]) -> tuple[requests.Response | None, str]:
    """GET with a few retries + exponential backoff. Returns (response, error)."""
    last_error = ""
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if response.status_code in _RETRY_STATUS:
                last_error = f"HTTP {response.status_code}"
                time.sleep(_BACKOFF_BASE**attempt + random.uniform(0, 0.4))
                continue
            response.raise_for_status()
            return response, ""
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(_BACKOFF_BASE**attempt + random.uniform(0, 0.4))
    return None, last_error or "request failed"


def _extract_body(content: bytes, url: str) -> str:
    """Try several extractors in order; return the first body that clears the
    minimum length. Having the HTML in hand, this recovers most pages that
    trafilatura's default pass misses."""
    # 1) trafilatura default (precision-leaning)
    text = trafilatura.extract(content, url=url, include_comments=False, include_tables=False)
    cleaned = clean_article_text(text) if text else ""
    if len(cleaned) >= MIN_ARTICLE_TEXT_CHARS:
        return cleaned

    # 2) trafilatura recall-leaning
    text = trafilatura.extract(
        content, url=url, include_comments=False, include_tables=False, favor_recall=True
    )
    cleaned = clean_article_text(text) if text else ""
    if len(cleaned) >= MIN_ARTICLE_TEXT_CHARS:
        return cleaned

    # 3) readability-lxml
    if _ReadabilityDocument is not None:
        try:
            summary_html = _ReadabilityDocument(content).summary(html_partial=True)
            text = lxml_html.fromstring(summary_html).text_content()
            cleaned = clean_article_text(text)
            if len(cleaned) >= MIN_ARTICLE_TEXT_CHARS:
                return cleaned
        except Exception:
            pass

    # 4) bare <p> aggregation — last resort for unusual templates
    try:
        tree = lxml_html.fromstring(content)
        paragraphs = [p.text_content().strip() for p in tree.xpath("//p")]
        text = "\n".join(p for p in paragraphs if len(p) > 40)
        cleaned = clean_article_text(text)
        if len(cleaned) >= MIN_ARTICLE_TEXT_CHARS:
            return cleaned
    except Exception:
        pass

    return ""


def _wayback_html(url: str, timeout: int, headers: dict[str, str]) -> tuple[bytes | None, str]:
    """Fetch the closest Wayback Machine snapshot of url. Archived copies carry
    the full article text and bypass live anti-bot / paywall blocks, so this is
    the main recovery path for sites that refuse a direct fetch."""
    try:
        api = "http://archive.org/wayback/available?url=" + requests.utils.quote(url, safe="")
        meta = requests.get(api, timeout=timeout).json()
        snap = meta.get("archived_snapshots", {}).get("closest", {})
        if not snap.get("available") or not snap.get("url"):
            return None, ""
        snap_url = snap["url"]
        response = requests.get(snap_url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        return response.content, snap_url
    except Exception:
        return None, ""


def extract_article(url: str, timeout: int, user_agent: str) -> dict[str, Any]:
    headers = _browser_headers(user_agent)

    response, request_error = _get(url, timeout, headers)

    body, used_content, method = "", None, "none"
    if response is not None:
        body = _extract_body(response.content, response.url)
        if body:
            used_content, method = response.content, "direct"

    # If the live page either didn't download or didn't yield a body, fall back
    # to the Wayback snapshot (covers both request_failed and extract_failed).
    if not body:
        wb_content, _wb_url = _wayback_html(url, timeout, headers)
        if wb_content:
            wb_body = _extract_body(wb_content, _wb_url or url)
            if wb_body:
                body, used_content, method = wb_body, wb_content, "wayback"

    final_url = response.url if response is not None else url

    webpage_published_at = ""
    if used_content is not None:
        try:
            webpage_published_at = extract_webpage_published_at(used_content, final_url)
        except Exception:
            webpage_published_at = ""

    if body:
        return {
            "original_url": final_url,
            "webpage_published_at": webpage_published_at,
            "article_text": body,
            "fetch_status": "success",
            "fetch_error": "",
            "fetch_method": method,
        }

    # Genuinely unobtainable: keep it honest — no body, surfaced for manual link
    # check only, never fed to the digest LLM.
    if response is None:
        status, error = "request_failed", request_error
    else:
        status, error = "extract_failed", (
            "Article text too short after all extractors; likely paywall/JS-only page."
        )
    return {
        "original_url": final_url,
        "webpage_published_at": "",
        "article_text": "",
        "fetch_status": status,
        "fetch_error": error,
        "fetch_method": "none",
    }


def extract_webpage_published_at(html: bytes | str, url: str) -> str:
    metadata = trafilatura.extract_metadata(html, default_url=url)
    if not metadata or not metadata.date:
        return ""
    return str(metadata.date)
