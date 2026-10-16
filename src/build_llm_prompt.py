from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
LLM_INPUT_PATH = ROOT_DIR / "data" / "llm_input.json"
LLM_PROMPT_PATH = ROOT_DIR / "data" / "llm_prompt.md"
PROMPT_TEMPLATE_PATH = ROOT_DIR / "prompts" / "digest_draft.md"
MAX_ARTICLE_TEXT_CHARS = 9000


def load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main() -> None:
    items = load_items(LLM_INPUT_PATH)
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt = render_prompt(template, items)
    LLM_PROMPT_PATH.write_text(prompt, encoding="utf-8")

    print(f"Loaded {len(items)} items from {LLM_INPUT_PATH}")
    print(f"Saved LLM prompt to {LLM_PROMPT_PATH}")


def render_prompt(template: str, items: list[dict[str, Any]]) -> str:
    lines = [template.rstrip(), ""]

    for index, item in enumerate(items, start=1):
        lines.extend(render_item(index, item))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_item(index: int, item: dict[str, Any]) -> list[str]:
    article_text = item.get("article_text", "")
    if len(article_text) > MAX_ARTICLE_TEXT_CHARS:
        article_text = article_text[:MAX_ARTICLE_TEXT_CHARS].rstrip() + "\n[TRUNCATED]"

    metadata = {
        "decision": item.get("decision"),
        "category": item.get("category"),
        "title": item.get("title"),
        "original_title": item.get("original_title"),
        "source": item.get("source"),
        "url": item.get("url"),
        "notes": item.get("notes"),
        "news_type": item.get("news_type"),
        "project_stage": item.get("project_stage"),
        "entities": item.get("entities", {}),
        "watch_reason": item.get("watch_reason"),
        "duplicate_sources": item.get("duplicate_sources", []),
        "duplicate_titles": item.get("duplicate_titles", []),
    }

    return [
        f"## Article {index}",
        "",
        "```json",
        json.dumps(metadata, ensure_ascii=False, indent=2),
        "```",
        "",
        "Article text:",
        "",
        "```text",
        article_text,
        "```",
    ]


if __name__ == "__main__":
    main()
