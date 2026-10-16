from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from generate_digest import load_env_file
from pipeline_paths import (
    add_run_date_arg,
    effective_run_date,
    resolve_pipeline_input_path,
    run_path,
    save_json_with_latest,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
PREFILTER_PROMPT_PATH = ROOT_DIR / "prompts" / "metadata_prefilter.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use an LLM to prefilter RSS metadata.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--min-priority",
        choices=["high", "medium", "low"],
        default=None,
        help="Minimum fetch priority saved to llm_prefiltered_news.json. Defaults to env or medium.",
    )
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(ROOT_DIR / ".env")
    config = load_config()
    min_priority = args.min_priority or config["min_priority"]
    discovered_path = resolve_prefilter_input_path(args.run_date)
    discovered_items = load_json(discovered_path)
    if args.limit is not None:
        discovered_items = discovered_items[: args.limit]

    prompt_template = PREFILTER_PROMPT_PATH.read_text(encoding="utf-8")
    client = create_client(config)

    input_signature = build_input_signature(discovered_items, discovered_path, prompt_template, config)
    run_date = effective_run_date(args.run_date)
    meta_path = run_path(ROOT_DIR, run_date, "llm_prefilter_input_meta.json")
    previous_signature = load_json_object(meta_path)
    results_path = run_path(ROOT_DIR, run_date, "llm_prefilter_results.json")
    all_results: list[dict[str, Any]] = []
    if previous_signature == input_signature:
        all_results = load_json(results_path)
    elif results_path.exists():
        print("Ignoring previous LLM prefilter results because the input set changed")
    result_by_id = {int(result["id"]): result for result in all_results if "id" in result}
    for batch_start in range(0, len(discovered_items), config["batch_size"]):
        batch = discovered_items[batch_start : batch_start + config["batch_size"]]
        batch_ids = set(range(batch_start + 1, batch_start + len(batch) + 1))
        if batch_ids.issubset(result_by_id):
            print(
                f"Skipping batch {batch_start + 1}-{batch_start + len(batch)} "
                f"of {len(discovered_items)}"
            )
            continue
        print(f"Prefiltering batch {batch_start + 1}-{batch_start + len(batch)} of {len(discovered_items)}")
        prompt = build_batch_prompt(prompt_template, batch, batch_start)
        output_text = create_prefilter_output(client, config, prompt)
        try:
            batch_results = parse_results(output_text)
        except (json.JSONDecodeError, ValueError) as exc:
            preview = output_text.strip()[:200].replace("\n", " ")
            print(
                f"WARN  Skipping batch {batch_start + 1}-{batch_start + len(batch)}: "
                f"could not parse model JSON ({exc}). Raw start: {preview!r}",
                file=sys.stderr,
            )
            continue
        for result in batch_results:
            result_by_id[int(result["id"])] = result
        all_results = [result_by_id[key] for key in sorted(result_by_id)]
        save_json_with_latest(
            ROOT_DIR, args.run_date, "llm_prefilter_results.json", all_results
        )
        save_json_with_latest(
            ROOT_DIR, args.run_date, "llm_prefilter_input_meta.json", input_signature
        )

    # The model sometimes returns only a subset of a batch (e.g. it lists the
    # items it would "fetch" and silently omits the ones it would "skip"). We
    # cannot tell "evaluated and skipped" from "never evaluated", so we re-prompt
    # any item without a decision in progressively smaller batches (ending at one
    # item each, which the model cannot reasonably omit) to force an explicit
    # verdict for every item.
    for gapfill_size in (12, 4, 1):
        missing_ids = [
            index
            for index in range(1, len(discovered_items) + 1)
            if index not in result_by_id
        ]
        if not missing_ids:
            break
        print(
            f"WARN  {len(missing_ids)} of {len(discovered_items)} items had no decision; "
            f"gap-fill re-prompting in batches of {gapfill_size}. Ids: {missing_ids}",
            file=sys.stderr,
        )
        for chunk_start in range(0, len(missing_ids), gapfill_size):
            chunk_ids = missing_ids[chunk_start : chunk_start + gapfill_size]
            pairs = [(item_id, discovered_items[item_id - 1]) for item_id in chunk_ids]
            output_text = create_prefilter_output(
                client, config, build_prompt_for_items(prompt_template, pairs)
            )
            try:
                chunk_results = parse_results(output_text)
            except (json.JSONDecodeError, ValueError) as exc:
                preview = output_text.strip()[:200].replace("\n", " ")
                print(
                    f"WARN  Gap-fill chunk {chunk_ids} could not parse model JSON ({exc}). "
                    f"Raw start: {preview!r}",
                    file=sys.stderr,
                )
                continue
            for result in chunk_results:
                result_id = int(result["id"])
                if result_id in chunk_ids and result_id not in result_by_id:
                    result_by_id[result_id] = result
        all_results = [result_by_id[key] for key in sorted(result_by_id)]
        save_json_with_latest(
            ROOT_DIR, args.run_date, "llm_prefilter_results.json", all_results
        )
        save_json_with_latest(
            ROOT_DIR, args.run_date, "llm_prefilter_input_meta.json", input_signature
        )

    unresolved_ids = [
        index
        for index in range(1, len(discovered_items) + 1)
        if index not in result_by_id
    ]
    if unresolved_ids:
        print(
            f"ERROR  {len(unresolved_ids)} of {len(discovered_items)} items still have NO LLM "
            f"decision after gap-fill (ids: {unresolved_ids}). The model never returned a verdict "
            "for these, so we cannot confirm it evaluated them -- refusing to emit a partial set. "
            "Re-run this step (already-decided items are cached and reused).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    prefiltered_items = []
    enriched_items = []
    for index, item in enumerate(discovered_items, start=1):
        result = result_by_id.get(index)
        if not result:
            continue
        enriched_item = dict(item)
        enriched_item.update(
            {
                "llm_prefilter_decision": result.get("decision", "skip"),
                "llm_prefilter_priority": result.get("priority", "low"),
                "llm_prefilter_news_type": result.get("news_type", "其他"),
                "llm_prefilter_reason": result.get("reason", ""),
            }
        )
        enriched_items.append(enriched_item)
        if (
            enriched_item["llm_prefilter_decision"] == "fetch"
            and priority_rank(enriched_item["llm_prefilter_priority"]) >= priority_rank(min_priority)
        ):
            prefiltered_items.append(enriched_item)

    prefiltered_items = apply_type_minimums(prefiltered_items, enriched_items, config)

    results_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "llm_prefilter_results.json", all_results
    )
    save_json_with_latest(
        ROOT_DIR, args.run_date, "llm_prefilter_input_meta.json", input_signature
    )
    prefiltered_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "llm_prefiltered_news.json", prefiltered_items
    )

    print(f"Loaded {len(discovered_items)} discovered items")
    print(f"Saved {len(all_results)} LLM prefilter decisions to {results_path}")
    print(f"Saved {len(prefiltered_items)} fetch candidates to {prefiltered_path}")
    print(f"Prefilter provider/model: {config['provider']} / {config['model']}")
    print(f"Minimum saved priority: {min_priority}")


def load_config() -> dict[str, Any]:
    provider = env_value("LLM_PREFILTER_PROVIDER", "OPENAI_PREFILTER_PROVIDER").strip().casefold()
    if not provider:
        provider = "deepseek" if env_value("DEEPSEEK_PREFILTER_API_KEY", "DEEPSEEK_API_KEY") else "openai"

    if provider == "deepseek":
        api_key = env_value("DEEPSEEK_PREFILTER_API_KEY", "DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set for DeepSeek prefiltering.")
        model = env_value(
            "LLM_PREFILTER_MODEL",
            "DEEPSEEK_PREFILTER_MODEL",
            default="deepseek-chat",
        )
        base_url = env_value(
            "LLM_PREFILTER_BASE_URL",
            "DEEPSEEK_BASE_URL",
            default="https://api.deepseek.com",
        )
        api_mode = "chat_completions"
    elif provider == "openai":
        api_key = env_value("OPENAI_PREFILTER_API_KEY", "OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        model = env_value(
            "LLM_PREFILTER_MODEL",
            "OPENAI_PREFILTER_MODEL",
            "OPENAI_MODEL",
            default="gpt-5.4-mini",
        )
        base_url = env_value("LLM_PREFILTER_BASE_URL", "OPENAI_BASE_URL")
        api_mode = "responses"
    else:
        raise RuntimeError("LLM_PREFILTER_PROVIDER must be either 'deepseek' or 'openai'.")

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "api_mode": api_mode,
        "model": model,
        "batch_size": int(env_value("LLM_PREFILTER_BATCH_SIZE", "OPENAI_PREFILTER_BATCH_SIZE", default="30")),
        "max_output_tokens": int(
            env_value("LLM_PREFILTER_MAX_OUTPUT_TOKENS", "OPENAI_PREFILTER_MAX_OUTPUT_TOKENS", default="6000")
        ),
        "reasoning_effort": env_value("OPENAI_PREFILTER_REASONING_EFFORT", default="low"),
        "min_priority": env_value("LLM_PREFILTER_MIN_PRIORITY", "OPENAI_PREFILTER_MIN_PRIORITY", default="medium")
        .strip()
        .casefold(),
        "type_minimums": load_type_minimums(),
    }


def env_value(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def create_client(config: dict[str, Any]) -> OpenAI:
    kwargs = {"api_key": config["api_key"]}
    if config.get("base_url"):
        kwargs["base_url"] = config["base_url"]
    return OpenAI(**kwargs)


def create_prefilter_output(client: OpenAI, config: dict[str, Any], prompt: str) -> str:
    if config.get("api_mode") == "chat_completions":
        response = client.chat.completions.create(
            model=config["model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config["max_output_tokens"],
            temperature=0,
        )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            print(
                f"WARN  Model hit max_tokens={config['max_output_tokens']} "
                "(finish_reason=length); JSON may be truncated.",
                file=sys.stderr,
            )
        return str(choice.message.content or "")

    response = client.responses.create(
        model=config["model"],
        input=prompt,
        max_output_tokens=config["max_output_tokens"],
        reasoning={"effort": config["reasoning_effort"]},
    )
    return str(response.output_text)


def load_type_minimums() -> dict[str, int]:
    raw_value = os.getenv("OPENAI_PREFILTER_TYPE_MINIMUMS", "").strip()
    minimums: dict[str, int] = {}
    if not raw_value:
        return minimums
    for part in raw_value.split(";"):
        if not part.strip() or "=" not in part:
            continue
        name, value = part.split("=", 1)
        try:
            minimums[normalize_news_type(name)] = int(value)
        except ValueError:
            continue
    return minimums


def resolve_prefilter_input_path(run_date: str | None) -> Path:
    actual_run_date = effective_run_date(run_date)
    sampled_path = run_path(ROOT_DIR, actual_run_date, "rss_prescreened_sample_news.json")
    if sampled_path.exists():
        return sampled_path
    prescreened_path = run_path(ROOT_DIR, actual_run_date, "rss_prescreened_news.json")
    if prescreened_path.exists():
        return prescreened_path
    return resolve_pipeline_input_path(ROOT_DIR, run_date, "discovered_news.json")


def build_input_signature(
    items: list[dict[str, Any]],
    input_path: Path,
    prompt_template: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    payload = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source", ""),
            "published_at": item.get("published_at", ""),
        }
        for item in items
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "input_file": input_path.name,
        "item_count": len(items),
        "provider": config["provider"],
        "model": config["model"],
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "prompt_fingerprint": hashlib.sha256(prompt_template.encode("utf-8")).hexdigest(),
    }


def build_compact_item(item_id: int, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "published_at": item.get("published_at", ""),
        "rss_summary": strip_html(item.get("rss_summary", "")),
        "search_keyword": item.get("search_keyword", ""),
        "search_group": item.get("search_group", ""),
        "search_pool": item.get("search_pool", ""),
        "search_lookback": item.get("search_lookback", ""),
    }


def build_prompt_for_items(
    template: str, id_item_pairs: list[tuple[int, dict[str, Any]]]
) -> str:
    compact_items = [build_compact_item(item_id, item) for item_id, item in id_item_pairs]
    return (
        template.rstrip()
        + "\n\n# 待判断新闻\n\n"
        + json.dumps(compact_items, ensure_ascii=False, indent=2)
    )


def build_batch_prompt(template: str, batch: list[dict[str, Any]], batch_start: int) -> str:
    pairs = [(batch_start + offset, item) for offset, item in enumerate(batch, start=1)]
    return build_prompt_for_items(template, pairs)


def parse_results(output_text: str) -> list[dict[str, Any]]:
    cleaned_text = output_text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.removeprefix("```json").removeprefix("```").strip()
        cleaned_text = cleaned_text.removesuffix("```").strip()
    parsed = json.loads(cleaned_text)
    if not isinstance(parsed, list):
        raise ValueError("Prefilter response must be a JSON list.")
    return [normalize_result(item) for item in parsed]


def normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    decision = str(item.get("decision", "skip")).strip().casefold()
    if decision not in {"fetch", "skip"}:
        decision = "skip"

    priority = str(item.get("priority", "low")).strip().casefold()
    if priority not in {"high", "medium", "low"}:
        priority = "low"

    return {
        "id": int(item.get("id")),
        "decision": decision,
        "priority": priority,
        "news_type": str(item.get("news_type", "其他")).strip() or "其他",
        "reason": str(item.get("reason", "")).strip(),
    }


def priority_rank(priority: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(priority).strip().casefold(), 0)


def apply_type_minimums(
    prefiltered_items: list[dict[str, Any]],
    enriched_items: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    type_minimums = config.get("type_minimums", {})
    if not type_minimums:
        return prefiltered_items

    selected_items = list(prefiltered_items)
    selected_keys = {item_key(item) for item in selected_items}

    for normalized_type, minimum in type_minimums.items():
        current_count = sum(
            1
            for item in selected_items
            if normalize_news_type(item.get("llm_prefilter_news_type", "")) == normalized_type
        )
        if current_count >= minimum:
            continue

        candidates = [
            item
            for item in enriched_items
            if normalize_news_type(item.get("llm_prefilter_news_type", "")) == normalized_type
            and item_key(item) not in selected_keys
            and item.get("llm_prefilter_decision") == "fetch"
        ]
        candidates.sort(key=prefilter_backfill_sort_key, reverse=True)

        for item in candidates:
            backfilled = dict(item)
            backfilled["llm_prefilter_minimum_backfill"] = True
            backfilled["llm_prefilter_reason"] = (
                f"{backfilled.get('llm_prefilter_reason', '')} "
                f"[type minimum backfill: {backfilled.get('llm_prefilter_news_type', '')}]"
            ).strip()
            selected_items.append(backfilled)
            selected_keys.add(item_key(backfilled))
            current_count += 1
            if current_count >= minimum:
                break

    return selected_items


def prefilter_backfill_sort_key(item: dict[str, Any]) -> tuple[int, int]:
    decision_rank = 1 if item.get("llm_prefilter_decision") == "fetch" else 0
    return (decision_rank, priority_rank(str(item.get("llm_prefilter_priority", "")).casefold()))


def item_key(item: dict[str, Any]) -> str:
    return str(item.get("google_news_url") or item.get("title") or id(item))


def normalize_news_type(value: Any) -> str:
    text = str(value or "").casefold()
    text = text.replace("/", " ").replace("\u3001", " ")
    text = " ".join(text.split())
    if any(
        term in text
        for term in [
            "\u653f\u6cbb",
            "\u653f\u5e9c\u6cbb\u7406",
            "\u6cbb\u7406",
            "\u9009\u4e3e",
            "governance",
            "politics",
            "election",
        ]
    ):
        return "politics_governance"
    if any(
        term in text
        for term in [
            "\u5b89\u5168",
            "\u793e\u4f1a",
            "\u5730\u7f18",
            "security",
            "risk",
        ]
    ):
        return "security_risk"
    return text


def strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip()


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
