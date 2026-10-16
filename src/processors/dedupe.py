from __future__ import annotations

import re
from typing import Any

from source_priority import source_priority_score, with_source_priority


TITLE_SIMILARITY_THRESHOLD = 0.5
ENTITY_OVERLAP_THRESHOLD = 2
# Even when two articles share strong entities (agency + 2 locations + a number),
# require at least this much title overlap before merging. Without it, unrelated
# same-topic stories that merely share generic places (Abuja, a home state) and a
# figure collapse together — e.g. two different Peter Obi stories merging on
# Abuja+Anambra+a naira amount. A genuine same-event duplicate clears this easily.
STRONG_ENTITY_TITLE_FLOOR = 0.2

STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
    "nigeria",
    "nigerian",
    "fg",
    "govt",
    "federal",
}


def dedupe_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[list[dict[str, Any]]] = []

    for article in sorted(articles, key=article_quality, reverse=True):
        matching_cluster = find_matching_cluster(article, clusters)
        if matching_cluster is None:
            clusters.append([article])
        else:
            matching_cluster.append(article)

    deduped_articles = [merge_cluster(cluster) for cluster in clusters]
    deduped_articles.sort(key=article_quality, reverse=True)
    return deduped_articles


def find_matching_cluster(
    article: dict[str, Any], clusters: list[list[dict[str, Any]]]
) -> list[dict[str, Any]] | None:
    for cluster in clusters:
        if any(are_duplicates(article, existing_article) for existing_article in cluster):
            return cluster
    return None


def are_duplicates(article_a: dict[str, Any], article_b: dict[str, Any]) -> bool:
    if same_url(article_a, article_b):
        return True

    title_similarity = jaccard_similarity(
        title_tokens(article_a.get("title", "")),
        title_tokens(article_b.get("title", "")),
    )
    entity_overlap = len(article_entities(article_a) & article_entities(article_b))
    same_type = article_a.get("news_type") == article_b.get("news_type")

    if title_similarity >= 0.72:
        return True

    if title_similarity >= TITLE_SIMILARITY_THRESHOLD and same_type:
        return True

    if title_similarity >= 0.35 and entity_overlap >= ENTITY_OVERLAP_THRESHOLD:
        return True

    if (
        same_type
        and title_similarity >= STRONG_ENTITY_TITLE_FLOOR
        and strong_entity_overlap(article_a, article_b)
    ):
        return True

    return False


def same_url(article_a: dict[str, Any], article_b: dict[str, Any]) -> bool:
    urls_a = article_urls(article_a)
    urls_b = article_urls(article_b)
    return bool(urls_a & urls_b)


def article_urls(article: dict[str, Any]) -> set[str]:
    return {
        url
        for url in [
            article.get("resolved_url"),
            article.get("original_url"),
            article.get("google_news_url"),
        ]
        if url
    }


def title_tokens(title: str) -> set[str]:
    normalized_title = re.sub(r"[^a-z0-9.%]+", " ", title.casefold())
    return {
        token
        for token in normalized_title.split()
        if len(token) > 2 and token not in STOPWORDS
    }


def article_entities(article: dict[str, Any]) -> set[str]:
    entities = article.get("entities", {})
    values: set[str] = set()
    for key in ["locations", "agencies", "companies", "financiers", "amounts", "distances"]:
        for value in entities.get(key, []):
            values.add(str(value).casefold())
    return values


def strong_entity_overlap(article_a: dict[str, Any], article_b: dict[str, Any]) -> bool:
    entities_a = article_a.get("entities", {})
    entities_b = article_b.get("entities", {})
    agency_overlap = normalized_overlap(entities_a.get("agencies", []), entities_b.get("agencies", []))
    location_overlap = normalized_overlap(entities_a.get("locations", []), entities_b.get("locations", []))
    numeric_overlap = normalized_overlap(
        numeric_signals(article_a),
        numeric_signals(article_b),
    )

    return agency_overlap >= 1 and location_overlap >= 2 and numeric_overlap >= 1


def normalized_overlap(values_a: list[str], values_b: list[str]) -> int:
    normalized_a = {str(value).casefold() for value in values_a if value}
    normalized_b = {str(value).casefold() for value in values_b if value}
    return len(normalized_a & normalized_b)


def numeric_signals(article: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            article.get("title", ""),
            article.get("article_text", "")[:1500],
        ]
    )
    return re.findall(r"\b\d[\d,.]*(?:m|mt|%|teus|tons|tonnes)?\b", text.casefold())


def jaccard_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def merge_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    primary_article = max(cluster, key=article_quality)
    duplicate_articles = [
        article for article in cluster if article is not primary_article
    ]

    merged_article = with_source_priority(primary_article)
    merged_article["duplicate_count"] = len(duplicate_articles)
    merged_article["duplicate_sources"] = unique_values(
        article.get("source", "") for article in duplicate_articles
    )
    merged_article["duplicate_titles"] = unique_values(
        article.get("title", "") for article in duplicate_articles
    )
    merged_article["duplicate_urls"] = unique_values(
        url for article in duplicate_articles for url in article_urls(article)
    )
    merged_article["cluster_size"] = len(cluster)
    return merged_article


def article_quality(article: dict[str, Any]) -> tuple[int, int, int, int]:
    fetch_score = 1 if article.get("fetch_status") == "success" else 0
    source_score = source_priority_score(article)
    relevance_score = int(article.get("relevance_score", 0))
    text_length = len(article.get("article_text", ""))
    return (fetch_score, source_score, relevance_score, text_length)


def unique_values(values: Any) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for value in values:
        if not value:
            continue
        normalized = str(value).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(str(value))
    return unique_items
