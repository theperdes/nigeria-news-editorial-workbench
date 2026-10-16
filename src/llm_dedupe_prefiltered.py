from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from generate_digest import load_env_file
from llm_prefilter import create_client, create_prefilter_output, load_config, strip_html
from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT_DIR / "prompts" / "prefilter_semantic_dedupe.md"
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
STOPWORDS = {
    "about",
    "after",
    "against",
    "amid",
    "and",
    "are",
    "before",
    "boost",
    "candidate",
    "candidates",
    "could",
    "from",
    "government",
    "governor",
    "governorship",
    "into",
    "latest",
    "minister",
    "nigeria",
    "nigerian",
    "over",
    "poll",
    "polls",
    "presidency",
    "presidential",
    "president",
    "says",
    "seeks",
    "state",
    "states",
    "the",
    "their",
    "this",
    "tinubu",
    "under",
    "warn",
    "warns",
    "with",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semantically dedupe LLM-prefiltered RSS candidates before article fetching."
    )
    parser.add_argument("--source-filename", default="llm_prefiltered_news.json")
    parser.add_argument("--output-filename", default="llm_prefiltered_news.json")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("LLM_DEDUPE_BATCH_SIZE", "40")),
        help="Number of candidates to send to the semantic dedupe model per request.",
    )
    parser.add_argument(
        "--batch-overlap",
        type=int,
        default=int(os.getenv("LLM_DEDUPE_BATCH_OVERLAP", "10")),
        help="Number of candidates to overlap between adjacent semantic dedupe batches.",
    )
    parser.add_argument(
        "--max-summary-chars",
        type=int,
        default=int(os.getenv("LLM_DEDUPE_MAX_SUMMARY_CHARS", "300")),
        help="Maximum RSS summary characters included per candidate.",
    )
    parser.add_argument("--dry-run", action="store_true")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(ROOT_DIR / ".env")
    input_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, args.source_filename)
    items = load_json(input_path)
    if len(items) < 2:
        print(f"Loaded {len(items)} candidates from {input_path}; nothing to dedupe")
        return

    config = load_config()
    result = dedupe_candidate_blocks(
        create_client(config),
        config,
        items,
        batch_size=args.batch_size,
        batch_overlap=args.batch_overlap,
        max_summary_chars=args.max_summary_chars,
    )
    deduped_items = apply_dedupe(items, result)

    result_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "llm_prefilter_semantic_dedupe.json", result
    )
    backup_path = None
    output_path = None
    if not args.dry_run:
        backup_path = save_json_with_latest(
            ROOT_DIR,
            args.run_date,
            "llm_prefiltered_news_before_semantic_dedupe.json",
            items,
        )
        output_path = save_json_with_latest(
            ROOT_DIR, args.run_date, args.output_filename, deduped_items
        )

    duplicate_count = len(items) - len(deduped_items)
    print(f"Loaded {len(items)} LLM fetch candidates from {input_path}")
    print(f"Collapsed {duplicate_count} semantic duplicate candidates")
    print(f"Saved semantic dedupe rationale to {result_path}")
    if backup_path:
        print(f"Saved pre-dedupe backup to {backup_path}")
    if output_path:
        print(f"Saved {len(deduped_items)} deduped fetch candidates to {output_path}")
    if args.dry_run:
        print("Dry run: llm_prefiltered_news.json was not changed")


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return data


def dedupe_candidate_blocks(
    client: Any,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    batch_size: int,
    batch_overlap: int,
    max_summary_chars: int,
) -> dict[str, Any]:
    blocks = build_candidate_blocks(items)
    covered_ids = {item_id for block in blocks for item_id in block}
    print(
        f"Built {len(blocks)} likely duplicate blocks "
        f"covering {len(covered_ids)} of {len(items)} candidates"
    )
    if not blocks:
        return {"groups": []}

    groups: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    request_number = 0
    for block_number, block_ids in enumerate(blocks, start=1):
        for chunk_ids in chunk_block_ids(block_ids, batch_size, batch_overlap):
            if len(chunk_ids) < 2:
                continue
            request_number += 1
            print(
                f"Semantic dedupe request {request_number}: "
                f"block {block_number}/{len(blocks)}, ids {describe_ids(chunk_ids)} "
                f"({len(chunk_ids)} candidates)"
            )
            chunk_items = [items[item_id - 1] for item_id in chunk_ids]
            prompt = build_prompt(
                chunk_items,
                item_ids=chunk_ids,
                max_summary_chars=max_summary_chars,
            )
            output_text = create_prefilter_output(client, config, prompt)
            try:
                batch_result = parse_result(output_text, valid_ids=set(chunk_ids))
            except (json.JSONDecodeError, ValueError) as exc:
                print(
                    f"WARN  Skipping dedupe request {request_number}: could not parse "
                    f"model JSON ({exc}). Keeping all {len(chunk_ids)} candidates.",
                    file=sys.stderr,
                )
                continue
            for group in batch_result["groups"]:
                group_ids = {int(group["keep_id"])}
                group_ids.update(int(value) for value in group.get("duplicate_ids", []))
                if group_ids & used_ids:
                    continue
                groups.append(group)
                used_ids.update(group_ids)

    return {"groups": groups}


def build_candidate_blocks(items: list[dict[str, Any]]) -> list[list[int]]:
    blocks = build_type_blocks(items)
    block_sets = [set(block) for block in blocks]
    for similarity_block in build_similarity_blocks(items):
        similarity_set = set(similarity_block)
        if any(similarity_set <= block_set for block_set in block_sets):
            continue
        blocks.append(similarity_block)
        block_sets.append(similarity_set)

    seen: set[tuple[int, ...]] = set()
    unique_blocks: list[list[int]] = []
    for block in sorted(blocks, key=lambda ids: (ids[0], len(ids), ids[-1])):
        key = tuple(block)
        if key in seen:
            continue
        seen.add(key)
        unique_blocks.append(block)
    return unique_blocks


def build_type_blocks(items: list[dict[str, Any]]) -> list[list[int]]:
    by_type: dict[str, list[int]] = {}
    for index, item in enumerate(items, start=1):
        news_type = str(item.get("llm_prefilter_news_type", "")).strip()
        if not news_type:
            continue
        by_type.setdefault(news_type, []).append(index)
    return [ids for ids in by_type.values() if len(ids) > 1]


def build_similarity_blocks(items: list[dict[str, Any]]) -> list[list[int]]:
    signatures = [build_item_signature(item) for item in items]
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index in range(len(items)):
        for right_index in range(left_index + 1, len(items)):
            if is_candidate_duplicate(signatures[left_index], signatures[right_index]):
                union(left_index, right_index)

    components: dict[int, list[int]] = {}
    for index in range(len(items)):
        root = find(index)
        components.setdefault(root, []).append(index + 1)
    return sorted(
        [ids for ids in components.values() if len(ids) > 1],
        key=lambda ids: (ids[0], len(ids)),
    )


def build_item_signature(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title", ""))
    summary = strip_html(item.get("rss_summary", ""))
    keyword = str(item.get("search_keyword", ""))
    news_type = str(item.get("llm_prefilter_news_type", ""))
    context = " ".join([title, summary[:600], keyword, news_type])
    return {
        "title": normalize_text(title),
        "title_tokens": content_tokens(title),
        "context_tokens": content_tokens(context),
        "strong_tokens": strong_tokens(context),
        "news_type": news_type.casefold(),
        "keyword": keyword.casefold(),
    }


def is_candidate_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    title_ratio = SequenceMatcher(None, left["title"], right["title"]).ratio()
    title_overlap = jaccard(left["title_tokens"], right["title_tokens"])
    context_overlap = jaccard(left["context_tokens"], right["context_tokens"])
    shared_strong = left["strong_tokens"] & right["strong_tokens"]
    same_type = bool(left["news_type"] and left["news_type"] == right["news_type"])
    same_keyword = bool(left["keyword"] and left["keyword"] == right["keyword"])

    if title_ratio >= 0.58 or title_overlap >= 0.34:
        return True
    if shared_strong and same_type and context_overlap >= 0.16:
        return True
    if len(shared_strong) >= 2 and context_overlap >= 0.12:
        return True
    if same_keyword and same_type and context_overlap >= 0.18:
        return True
    return False


def normalize_text(value: str) -> str:
    return " ".join(token.casefold() for token in TOKEN_RE.findall(value))


def content_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(value)
        if len(token) >= 3 and token.casefold() not in STOPWORDS
    }


def strong_tokens(value: str) -> set[str]:
    tokens = {
        token.casefold()
        for token in TOKEN_RE.findall(value)
        if (len(token) >= 5 or any(char.isdigit() for char in token))
        and token.casefold() not in STOPWORDS
    }
    tokens.update(token.casefold() for token in ACRONYM_RE.findall(value))
    tokens.update(NUMBER_RE.findall(value))
    return tokens


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def chunk_block_ids(
    block_ids: list[int],
    batch_size: int,
    batch_overlap: int,
) -> list[list[int]]:
    if len(block_ids) <= batch_size:
        return [block_ids]
    if batch_overlap >= batch_size:
        raise ValueError("--batch-overlap must be smaller than --batch-size")
    step = batch_size - batch_overlap
    return [block_ids[start : start + batch_size] for start in range(0, len(block_ids), step)]


def describe_ids(item_ids: list[int]) -> str:
    if not item_ids:
        return ""
    if item_ids == list(range(item_ids[0], item_ids[-1] + 1)):
        return f"{item_ids[0]}-{item_ids[-1]}"
    if len(item_ids) <= 10:
        return ",".join(str(item_id) for item_id in item_ids)
    head = ",".join(str(item_id) for item_id in item_ids[:8])
    return f"{head},...{item_ids[-1]}"


def dedupe_in_batches(
    client: Any,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    batch_size: int,
    batch_overlap: int,
    max_summary_chars: int,
) -> dict[str, Any]:
    if batch_size < 2:
        raise ValueError("--batch-size must be at least 2")
    if batch_overlap < 0:
        raise ValueError("--batch-overlap cannot be negative")
    if batch_overlap >= batch_size:
        raise ValueError("--batch-overlap must be smaller than --batch-size")

    groups: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    step = batch_size - batch_overlap
    batch_number = 0
    for batch_start in range(0, len(items), step):
        batch = items[batch_start : batch_start + batch_size]
        if len(batch) < 2:
            continue
        batch_number += 1
        batch_first_id = batch_start + 1
        batch_last_id = batch_start + len(batch)
        print(
            f"Semantic dedupe batch {batch_number}: "
            f"{batch_first_id}-{batch_last_id} of {len(items)}"
        )
        prompt = build_prompt(
            batch,
            start_id=batch_first_id,
            max_summary_chars=max_summary_chars,
        )
        output_text = create_prefilter_output(client, config, prompt)
        batch_result = parse_result(
            output_text,
            valid_ids=set(range(batch_first_id, batch_last_id + 1)),
        )
        for group in batch_result["groups"]:
            group_ids = {int(group["keep_id"])}
            group_ids.update(int(value) for value in group.get("duplicate_ids", []))
            if group_ids & used_ids:
                continue
            groups.append(group)
            used_ids.update(group_ids)

    return {"groups": groups}


def build_prompt(
    items: list[dict[str, Any]],
    *,
    start_id: int = 1,
    item_ids: list[int] | None = None,
    max_summary_chars: int = 300,
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    if item_ids is None:
        item_ids = list(range(start_id, start_id + len(items)))
    compact_items = [
        {
            "id": index,
            "title": item.get("title", ""),
            "source": item.get("source", ""),
            "published_at": item.get("published_at", ""),
            "rss_summary": truncate_text(
                strip_html(item.get("rss_summary", "")),
                max_summary_chars,
            ),
            "search_keyword": item.get("search_keyword", ""),
            "search_group": item.get("search_group", ""),
            "search_pool": item.get("search_pool", ""),
            "llm_priority": item.get("llm_prefilter_priority", ""),
            "llm_news_type": item.get("llm_prefilter_news_type", ""),
        }
        for index, item in zip(item_ids, items, strict=True)
    ]
    return (
        template.rstrip()
        + "\n\n# 待语义去重候选\n\n"
        + json.dumps(compact_items, ensure_ascii=False, indent=2)
    )


def truncate_text(value: str, max_chars: int) -> str:
    value = " ".join(value.split())
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def parse_result(output_text: str, valid_ids: set[int]) -> dict[str, Any]:
    cleaned_text = output_text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.removeprefix("```json").removeprefix("```").strip()
        cleaned_text = cleaned_text.removesuffix("```").strip()
    parsed = json.loads(cleaned_text)
    if not isinstance(parsed, dict):
        raise ValueError("Semantic dedupe response must be a JSON object.")

    used_ids: set[int] = set()
    groups = []
    for group in parsed.get("groups", []):
        keep_id, raw_duplicate_ids, event, reason = normalize_group(group)
        if keep_id not in valid_ids or keep_id in used_ids:
            continue
        duplicate_ids = []
        for value in raw_duplicate_ids:
            duplicate_id = safe_int(value)
            if (
                duplicate_id in valid_ids
                and duplicate_id != keep_id
                and duplicate_id not in used_ids
            ):
                duplicate_ids.append(duplicate_id)
        if not duplicate_ids:
            continue
        used_ids.add(keep_id)
        used_ids.update(duplicate_ids)
        groups.append(
            {
                "event": event,
                "keep_id": keep_id,
                "duplicate_ids": duplicate_ids,
                "reason": reason,
            }
        )
    return {"groups": groups}


def normalize_group(group: Any) -> tuple[int | None, list[Any], str, str]:
    if isinstance(group, dict):
        return (
            safe_int(group.get("keep_id")),
            list(group.get("duplicate_ids", [])),
            str(group.get("event", "")).strip(),
            str(group.get("reason", "")).strip(),
        )
    if isinstance(group, list) and len(group) >= 2:
        return safe_int(group[0]), list(group[1] or []), "", ""
    return None, [], "", ""


def apply_dedupe(items: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    item_by_id = {index: item for index, item in enumerate(items, start=1)}
    duplicate_ids: set[int] = set()
    groups_by_keep: dict[int, list[dict[str, Any]]] = {}
    for group in result.get("groups", []):
        keep_id = int(group["keep_id"])
        groups_by_keep.setdefault(keep_id, []).append(group)
        duplicate_ids.update(int(value) for value in group.get("duplicate_ids", []))

    deduped_items = []
    for index, item in item_by_id.items():
        if index in duplicate_ids:
            continue
        deduped_item = dict(item)
        groups = groups_by_keep.get(index, [])
        if groups:
            duplicate_titles = list(deduped_item.get("semantic_duplicate_titles", []))
            duplicate_sources = list(deduped_item.get("semantic_duplicate_sources", []))
            duplicate_urls = list(deduped_item.get("semantic_duplicate_urls", []))
            for group in groups:
                for duplicate_id in group.get("duplicate_ids", []):
                    duplicate = item_by_id.get(int(duplicate_id), {})
                    append_unique(duplicate_titles, duplicate.get("title"))
                    append_unique(duplicate_sources, duplicate.get("source"))
                    append_unique(
                        duplicate_urls,
                        duplicate.get("url")
                        or duplicate.get("google_news_url")
                        or duplicate.get("resolved_url"),
                    )
            deduped_item["semantic_duplicate_count"] = len(duplicate_titles)
            deduped_item["semantic_duplicate_titles"] = duplicate_titles
            deduped_item["semantic_duplicate_sources"] = duplicate_sources
            deduped_item["semantic_duplicate_urls"] = duplicate_urls
            deduped_item["semantic_duplicate_groups"] = groups
        deduped_items.append(deduped_item)
    return deduped_items


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
