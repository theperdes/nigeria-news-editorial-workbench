from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT_DIR / "data" / "review.md"
FINAL_DIGEST_PATH = ROOT_DIR / "data" / "final_digest.md"


FIELD_PATTERNS = {
    "decision": re.compile(
        r"^- Review decision:\s*\[(?P<include>[ xX])\]\s*include\s*"
        r"\[(?P<maybe>[ xX])\]\s*maybe\s*"
        r"\[(?P<exclude>[ xX])\]\s*exclude\s*$"
    ),
    "suggested_category": re.compile(r"^- Suggested category:\s*(?P<value>.*)$"),
    "suggested_title": re.compile(r"^- Suggested title:\s*(?P<value>.*)$"),
    "final_category": re.compile(r"^- Final category:\s*(?P<value>.*)$"),
    "final_title": re.compile(r"^- Final title:\s*(?P<value>.*)$"),
    "notes": re.compile(r"^- Notes:\s*(?P<value>.*)$"),
    "source": re.compile(r"^- Source:\s*(?P<value>.*)$"),
    "url": re.compile(r"^- URL:\s*(?P<value>.*)$"),
}


def main() -> None:
    articles = parse_review(REVIEW_PATH.read_text(encoding="utf-8"))
    included_articles = [article for article in articles if article["decision"] == "include"]
    maybe_articles = [article for article in articles if article["decision"] == "maybe"]

    digest = render_digest(included_articles, maybe_articles)
    FINAL_DIGEST_PATH.write_text(digest, encoding="utf-8")

    print(f"Parsed {len(articles)} reviewed articles from {REVIEW_PATH}")
    print(f"Included {len(included_articles)} articles")
    print(f"Maybe {len(maybe_articles)} articles")
    print(f"Saved digest to {FINAL_DIGEST_PATH}")


def parse_review(text: str) -> list[dict[str, str]]:
    sections = re.split(r"(?m)^###\s+\d+\.\s+", text)
    articles: list[dict[str, str]] = []

    for section in sections[1:]:
        lines = section.splitlines()
        if not lines:
            continue

        article = {
            "original_title": lines[0].strip(),
            "decision": "unreviewed",
            "has_decision": False,
            "suggested_category": "",
            "suggested_title": "",
            "final_category": "",
            "final_title": "",
            "notes": "",
            "source": "",
            "url": "",
        }

        for line in lines:
            update_article_from_line(article, line)

        articles.append(article)

    return articles


def update_article_from_line(article: dict[str, str], line: str) -> None:
    decision_match = FIELD_PATTERNS["decision"].match(line)
    if decision_match:
        article["decision"] = parse_decision(decision_match)
        article["has_decision"] = True
        return

    for field, pattern in FIELD_PATTERNS.items():
        if field == "decision":
            continue
        match = pattern.match(line)
        if match:
            article[field] = match.group("value").strip()
            return


def parse_decision(match: re.Match[str]) -> str:
    if match.group("include").strip().casefold() == "x":
        return "include"
    if match.group("maybe").strip().casefold() == "x":
        return "maybe"
    if match.group("exclude").strip().casefold() == "x":
        return "exclude"
    return "unreviewed"


def render_digest(
    included_articles: list[dict[str, str]],
    maybe_articles: list[dict[str, str]],
) -> str:
    lines = ["# 尼日利亚新闻日报", ""]
    grouped_articles = group_by_category(included_articles)

    for category, articles in grouped_articles.items():
        lines.extend([f"## {category}", ""])
        for index, article in enumerate(articles, start=1):
            lines.extend(render_digest_article(index, article))
            lines.append("")

    if maybe_articles:
        lines.extend(["## 备选新闻", ""])
        for index, article in enumerate(maybe_articles, start=1):
            lines.extend(render_digest_article(index, article))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def group_by_category(articles: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped_articles: dict[str, list[dict[str, str]]] = defaultdict(list)
    for article in articles:
        grouped_articles[article_category(article)].append(article)
    return dict(grouped_articles)


def render_digest_article(index: int, article: dict[str, str]) -> list[str]:
    lines = [f"{index}. {article_title(article)}"]
    if article["url"]:
        lines.append(f"链接：{article['url']}")
    return lines


def article_category(article: dict[str, str]) -> str:
    return article["final_category"] or article["suggested_category"] or "其他"


def article_title(article: dict[str, str]) -> str:
    return article["final_title"] or article["suggested_title"] or article["original_title"]


if __name__ == "__main__":
    main()
