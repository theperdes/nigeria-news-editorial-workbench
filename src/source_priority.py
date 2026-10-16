from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config" / "source_priority.yaml"


@lru_cache(maxsize=1)
def load_source_priority_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def source_priority(article: dict[str, Any]) -> dict[str, Any]:
    config = load_source_priority_config()
    source = str(article.get("source", "") or "")
    base_score, tier = base_source_score(source, config)
    bonuses: list[str] = []
    score = base_score

    if is_professional_source(source, config) and has_terms(article, config.get("professional_domain_terms", [])):
        bonus = int(config.get("professional_domain_bonus", 0) or 0)
        if bonus:
            score += bonus
            bonuses.append("professional_domain")

    if is_local_detail_source(source, config) and has_terms(article, config.get("local_detail_terms", [])):
        bonus = int(config.get("local_detail_bonus", 0) or 0)
        if bonus:
            score += bonus
            bonuses.append("local_detail")

    return {
        "source_priority_score": score,
        "source_priority_base_score": base_score,
        "source_priority_tier": tier,
        "source_priority_bonuses": bonuses,
    }


def source_priority_score(article: dict[str, Any]) -> int:
    existing = article.get("source_priority_score")
    if existing is not None:
        try:
            return int(existing)
        except (TypeError, ValueError):
            pass
    return int(source_priority(article)["source_priority_score"])


def with_source_priority(article: dict[str, Any]) -> dict[str, Any]:
    updated = dict(article)
    updated.update(source_priority(article))
    return updated


def base_source_score(source: str, config: dict[str, Any]) -> tuple[int, str]:
    default_score = int(config.get("default_score", 50) or 50)
    for tier, tier_config in (config.get("tiers", {}) or {}).items():
        for candidate in tier_config.get("sources", []) or []:
            if source_matches(source, str(candidate)):
                return int(tier_config.get("score", default_score) or default_score), str(tier)
    return default_score, "default"


def is_professional_source(source: str, config: dict[str, Any]) -> bool:
    professional = (config.get("tiers", {}) or {}).get("professional", {})
    return any(source_matches(source, str(candidate)) for candidate in professional.get("sources", []) or [])


def is_local_detail_source(source: str, config: dict[str, Any]) -> bool:
    return any(source_matches(source, str(candidate)) for candidate in config.get("local_detail_sources", []) or [])


def source_matches(source: str, candidate: str) -> bool:
    normalized_source = normalize_source(source)
    normalized_candidate = normalize_source(candidate)
    if not normalized_source or not normalized_candidate:
        return False
    return normalized_source == normalized_candidate or normalized_candidate in normalized_source


def normalize_source(value: str) -> str:
    text = str(value or "").casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9+]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_terms(article: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return False
    text = " ".join(
        str(article.get(field, "") or "")
        for field in ["title", "source", "rss_summary", "llm_prefilter_news_type", "news_type", "article_text"]
    ).casefold()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(str(term).casefold())}(?![a-z0-9])", text) for term in terms)
