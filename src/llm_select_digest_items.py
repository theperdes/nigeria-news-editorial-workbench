from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from generate_digest import load_env_file
from llm_prefilter import create_client, create_prefilter_output, load_config
from pipeline_paths import add_run_date_arg, effective_run_date, resolve_pipeline_input_path, run_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT_DIR / "prompts" / "digest_selection.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an LLM to semantically dedupe and trim reviewed digest input."
    )
    parser.add_argument("--target-count", type=int, default=5)
    parser.add_argument("--min-count", type=int, default=4)
    parser.add_argument("--source-filename", default="llm_input.json")
    parser.add_argument("--output-filename", default="llm_input.json")
    parser.add_argument("--dry-run", action="store_true")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(ROOT_DIR / ".env")
    config = load_config()
    run_date = effective_run_date(args.run_date)
    input_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, args.source_filename)
    items = load_json(input_path)
    if not items:
        raise RuntimeError(f"No digest input items found in {input_path}")

    prompt = build_prompt(items, args.target_count, args.min_count)
    output_text = create_prefilter_output(create_client(config), config, prompt)
    selection = parse_selection(output_text, len(items), args.target_count)
    selected_items = apply_selection(items, selection)

    selection_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "llm_digest_selection.json", selection
    )

    backup_path = None
    if not args.dry_run:
        backup_path = save_json_with_latest(
            ROOT_DIR, args.run_date, "llm_input_before_selection.json", items
        )
        output_path = save_json_with_latest(
            ROOT_DIR, args.run_date, args.output_filename, selected_items
        )
    else:
        output_path = None

    print(f"Loaded {len(items)} reviewed digest items from {input_path}")
    print(f"Selected {len(selected_items)} final digest items")
    print(f"Saved selection rationale to {selection_path}")
    if backup_path:
        print(f"Saved pre-selection backup to {backup_path}")
    if output_path:
        print(f"Saved selected digest input to {output_path}")
    if args.dry_run:
        print("Dry run: llm_input.json was not changed")


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return data


def build_prompt(items: list[dict[str, Any]], target_count: int, min_count: int) -> str:
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    compact_items = [
        {
            "id": index,
            "decision": item.get("decision", ""),
            "category": item.get("category", ""),
            "title": item.get("title", ""),
            "original_title": item.get("original_title", ""),
            "source": item.get("source", ""),
            "news_type": item.get("news_type", ""),
            "project_stage": item.get("project_stage", ""),
            "relevance_score": item.get("relevance_score", 0),
            "watch_reason": item.get("watch_reason", ""),
            "duplicate_titles": item.get("duplicate_titles", [])[:5],
            "notes": item.get("notes", ""),
            "article_excerpt": str(item.get("article_text", ""))[:1200],
        }
        for index, item in enumerate(items, start=1)
    ]
    return (
        prompt_template.rstrip()
        + f"\n\n# 本次目标\n\n最终尽量保留 {target_count} 条；"
        + f"如果高价值且互不重复的新闻不足，可少到 {min_count} 条。"
        + "\n\n# 待终选新闻\n\n"
        + json.dumps(compact_items, ensure_ascii=False, indent=2)
    )


def parse_selection(output_text: str, item_count: int, target_count: int) -> dict[str, Any]:
    cleaned_text = output_text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.removeprefix("```json").removeprefix("```").strip()
        cleaned_text = cleaned_text.removesuffix("```").strip()
    parsed = json.loads(cleaned_text)
    if not isinstance(parsed, dict):
        raise ValueError("Selection response must be a JSON object.")

    valid_ids = set(range(1, item_count + 1))
    selected_ids = []
    seen_ids: set[int] = set()
    for raw_id in parsed.get("selected_ids", []):
        item_id = int(raw_id)
        if item_id not in valid_ids or item_id in seen_ids:
            continue
        selected_ids.append(item_id)
        seen_ids.add(item_id)

    if not selected_ids:
        raise ValueError("Selection response did not include any valid selected_ids.")
    if len(selected_ids) > max(target_count + 2, target_count):
        selected_ids = selected_ids[:target_count]

    parsed["selected_ids"] = selected_ids
    parsed["groups"] = normalize_groups(parsed.get("groups", []), valid_ids)
    parsed["excluded"] = normalize_excluded(parsed.get("excluded", []), valid_ids)
    return parsed


def normalize_groups(groups: Any, valid_ids: set[int]) -> list[dict[str, Any]]:
    normalized = []
    if not isinstance(groups, list):
        return normalized
    for group in groups:
        if not isinstance(group, dict):
            continue
        keep_id = safe_int(group.get("keep_id"))
        duplicate_ids = [
            item_id
            for item_id in (safe_int(value) for value in group.get("duplicate_ids", []))
            if item_id in valid_ids and item_id != keep_id
        ]
        if keep_id not in valid_ids and not duplicate_ids:
            continue
        normalized.append(
            {
                "event": str(group.get("event", "")).strip(),
                "keep_id": keep_id if keep_id in valid_ids else None,
                "duplicate_ids": duplicate_ids,
                "reason": str(group.get("reason", "")).strip(),
            }
        )
    return normalized


def normalize_excluded(excluded: Any, valid_ids: set[int]) -> list[dict[str, Any]]:
    normalized = []
    if not isinstance(excluded, list):
        return normalized
    for item in excluded:
        if not isinstance(item, dict):
            continue
        item_id = safe_int(item.get("id"))
        if item_id not in valid_ids:
            continue
        normalized.append({"id": item_id, "reason": str(item.get("reason", "")).strip()})
    return normalized


def apply_selection(items: list[dict[str, Any]], selection: dict[str, Any]) -> list[dict[str, Any]]:
    item_by_id = {index: item for index, item in enumerate(items, start=1)}
    group_by_keep: dict[int, list[dict[str, Any]]] = {}
    group_by_duplicate: dict[int, dict[str, Any]] = {}
    for group in selection.get("groups", []):
        keep_id = group.get("keep_id")
        if isinstance(keep_id, int):
            group_by_keep.setdefault(keep_id, []).append(group)
        for duplicate_id in group.get("duplicate_ids", []):
            group_by_duplicate[int(duplicate_id)] = group

    selected_items = []
    for item_id in selection["selected_ids"]:
        item = dict(item_by_id[item_id])
        item["llm_selection_id"] = item_id
        item["llm_selection_reason"] = selection_reason(item_id, selection)
        duplicate_titles = list(item.get("duplicate_titles", []))
        duplicate_sources = list(item.get("duplicate_sources", []))
        duplicate_urls = list(item.get("duplicate_urls", []))
        for group in group_by_keep.get(item_id, []):
            for duplicate_id in group.get("duplicate_ids", []):
                duplicate = item_by_id.get(int(duplicate_id), {})
                append_unique(duplicate_titles, duplicate.get("title") or duplicate.get("original_title"))
                append_unique(duplicate_sources, duplicate.get("source"))
                append_unique(duplicate_urls, duplicate.get("url"))
        if duplicate_titles:
            item["duplicate_titles"] = duplicate_titles
        if duplicate_sources:
            item["duplicate_sources"] = duplicate_sources
        if duplicate_urls:
            item["duplicate_urls"] = duplicate_urls
        item["llm_semantic_duplicate_groups"] = group_by_keep.get(item_id, [])
        selected_items.append(item)

    for duplicate_id, group in group_by_duplicate.items():
        if duplicate_id in selection["selected_ids"]:
            continue
    return selected_items


def selection_reason(item_id: int, selection: dict[str, Any]) -> str:
    for group in selection.get("groups", []):
        if group.get("keep_id") == item_id and group.get("reason"):
            return str(group["reason"])
    for excluded in selection.get("excluded", []):
        if excluded.get("id") == item_id:
            return str(excluded.get("reason", ""))
    return ""


def append_unique(values: list[Any], value: Any) -> None:
    if not value:
        return
    normalized = str(value).casefold()
    if all(str(existing).casefold() != normalized for existing in values):
        values.append(value)


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
