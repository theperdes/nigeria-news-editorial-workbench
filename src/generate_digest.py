from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openai import OpenAI
from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest, write_text_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]
ARTICLE_PROMPT_PATH = ROOT_DIR / "prompts" / "article_draft.md"
LLM_DRAFTS_PATH = ROOT_DIR / "data" / "llm_drafts.json"
LLM_OUTPUT_PATH = ROOT_DIR / "data" / "llm_output.md"
LLM_RUN_PATH = ROOT_DIR / "data" / "llm_run.json"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FIXED_CATEGORY_ORDER = [
    "基础设施与项目动态",
    "电力与新能源",
    "油气与能源化工",
    "工业、农业与产业链",
    "融资、金融机构与投资平台",
    "政策、监管与财政预算",
    "双边/多边合作与对外关系",
    "政治动态与政府治理",
    "安全、社会与地缘风险",
    "其他",
]
CATEGORY_ALIASES = {
    "基础设施/项目动态": "基础设施与项目动态",
    "农业与产业链": "工业、农业与产业链",
    "房地产/项目机会": "基础设施与项目动态",
}


def load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    load_env_file(ROOT_DIR / ".env")
    load_env_file(ROOT_DIR / ".env.deepseek", override=True)
    config = load_config()
    llm_input_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, "llm_input.json")
    items = load_json(llm_input_path)
    if args.limit is not None:
        items = items[: args.limit]
    prompt_template = ARTICLE_PROMPT_PATH.read_text(encoding="utf-8")

    client = create_client(config)
    drafts: list[dict[str, Any]] = []

    for index, item in enumerate(items, start=1):
        print(f"Generating draft {index}/{len(items)}: {item.get('title', '')[:90]}")
        prompt = build_article_prompt(
            prompt_template,
            item,
            config,
            summary_only=args.summary_only,
        )
        output_text = create_model_output(client, config, prompt)
        draft = parse_model_json(output_text)
        draft["_source"] = {
            "decision": item.get("decision"),
            "original_title": item.get("original_title"),
            "source": item.get("source"),
            "url": item.get("url"),
            "duplicate_count": item.get("duplicate_count", 0),
            "duplicate_sources": item.get("duplicate_sources", []),
        }
        drafts.append(draft)
        save_json_with_latest(ROOT_DIR, args.run_date, "llm_drafts.json", drafts)

    output = render_digest(drafts, args.run_date, summary_only=args.summary_only)
    output_path = write_text_with_latest(ROOT_DIR, args.run_date, "llm_output.md", output)
    drafts_path = save_json_with_latest(ROOT_DIR, args.run_date, "llm_drafts.json", drafts)
    run_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "llm_run.json", build_run_metadata(config, items, drafts)
    )

    print(f"Loaded LLM input from {llm_input_path}")
    print(f"Saved drafts to {drafts_path}")
    print(f"Saved digest to {output_path}")
    print(f"Saved run metadata to {run_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reviewed news digest with the configured LLM provider."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N LLM input items.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Suppress interpretation and background text for this run.",
    )
    add_run_date_arg(parser)
    return parser.parse_args()


def load_config() -> dict[str, Any]:
    provider = env_value("LLM_DIGEST_PROVIDER", "DIGEST_PROVIDER").strip().casefold()
    if not provider:
        provider = "deepseek" if env_value("DEEPSEEK_DIGEST_API_KEY", "DEEPSEEK_API_KEY") else "openai"

    if provider == "deepseek":
        api_key = env_value("DEEPSEEK_DIGEST_API_KEY", "DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set for DeepSeek digest generation.")
        model = env_value(
            "LLM_DIGEST_MODEL",
            "DEEPSEEK_DIGEST_MODEL",
            default="deepseek-chat",
        )
        base_url = env_value(
            "LLM_DIGEST_BASE_URL",
            "DEEPSEEK_BASE_URL",
            default="https://api.deepseek.com",
        )
        api_mode = "chat_completions"
    elif provider == "openai":
        api_key = env_value("OPENAI_DIGEST_API_KEY", "OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Create .env from .env.example first.")
        model = env_value(
            "LLM_DIGEST_MODEL",
            "OPENAI_DIGEST_MODEL",
            "OPENAI_MODEL",
            default="gpt-5.4-mini",
        )
        base_url = env_value("LLM_DIGEST_BASE_URL", "OPENAI_BASE_URL")
        api_mode = "responses"
    else:
        raise RuntimeError("LLM_DIGEST_PROVIDER must be either 'deepseek' or 'openai'.")

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "api_mode": api_mode,
        "model": model,
        "max_output_tokens": int(
            env_value(
                "LLM_DIGEST_MAX_OUTPUT_TOKENS",
                "DEEPSEEK_DIGEST_MAX_OUTPUT_TOKENS",
                "OPENAI_MAX_OUTPUT_TOKENS",
                default="2500",
            )
        ),
        "reasoning_effort": env_value(
            "OPENAI_DIGEST_REASONING_EFFORT",
            "OPENAI_REASONING_EFFORT",
            default="low",
        ).strip(),
        "max_article_text_chars": int(env_value("MAX_ARTICLE_TEXT_CHARS", default="9000")),
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


def build_article_prompt(
    template: str,
    item: dict[str, Any],
    config: dict[str, Any],
    *,
    summary_only: bool = False,
) -> str:
    prompt_item = dict(item)
    article_text = prompt_item.get("article_text", "")
    max_chars = config["max_article_text_chars"]
    if len(article_text) > max_chars:
        prompt_item["article_text"] = article_text[:max_chars].rstrip() + "\n[TRUNCATED]"

    prompt = (
        template.rstrip()
        + "\n\n```json\n"
        + json.dumps(prompt_item, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    if summary_only:
        prompt += (
            "\n# 本次运行的输出覆盖要求\n\n"
            "将 `interpretation` 和 `note` 都设为空字符串。本次只生成事实性新闻总结，"
            "不要输出分析、判断或背景补充。\n"
        )
    return prompt


def create_model_output(client: OpenAI, config: dict[str, Any], prompt: str) -> str:
    if config["api_mode"] == "chat_completions":
        response = client.chat.completions.create(
            model=config["model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config["max_output_tokens"],
            temperature=0,
        )
        return str(response.choices[0].message.content or "")

    kwargs: dict[str, Any] = {
        "model": config["model"],
        "input": prompt,
        "max_output_tokens": config["max_output_tokens"],
        "reasoning": {"effort": config["reasoning_effort"]},
    }

    response = client.responses.create(**kwargs)
    return str(response.output_text)


def parse_model_json(output_text: str) -> dict[str, Any]:
    cleaned_text = output_text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.removeprefix("```json").removeprefix("```").strip()
        cleaned_text = cleaned_text.removesuffix("```").strip()

    try:
        parsed = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        return {
            "category": "其他",
            "short_title": "模型输出解析失败",
            "body": output_text.strip(),
            "interpretation": f"模型返回内容不是合法JSON：{exc}",
            "note": "",
            "url": "",
            "_parse_error": str(exc),
        }

    return normalize_draft(parsed)


def normalize_draft(draft: dict[str, Any]) -> dict[str, Any]:
    category = str(draft.get("category", "安全、社会与地缘风险")).strip()
    category = CATEGORY_ALIASES.get(category, category)
    if category not in FIXED_CATEGORY_ORDER:
        category = "其他"

    return {
        "category": category,
        "short_title": normalize_short_title(str(draft.get("short_title", ""))),
        "body": clean_model_text(str(draft.get("body", ""))),
        "interpretation": clean_model_text(str(draft.get("interpretation", ""))),
        "note": clean_model_text(str(draft.get("note", ""))),
        "url": clean_url(str(draft.get("url", "")).strip()),
    }


def normalize_short_title(text: str) -> str:
    # The model is responsible for producing a complete, length-bounded short
    # title (see prompts/article_draft.md). We intentionally do not truncate or
    # rewrite it here, because hard-truncating produced mid-word stubs like
    # "增长计" / "达成天然气" in the overview.
    return clean_model_text(text)


def clean_model_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"\s*\(\[[^\]]+\]\([^)]+\)\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", lambda match: clean_url(match.group(0)), cleaned)
    return cleaned.strip()


def clean_url(url: str) -> str:
    if not url:
        return ""
    trailing = ""
    while url and url[-1] in ").,，。":
        trailing = url[-1] + trailing
        url = url[:-1]
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url + trailing
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)) + trailing


def render_digest(
    drafts: list[dict[str, Any]],
    run_date: str | None = None,
    *,
    summary_only: bool = False,
) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for draft in drafts:
        grouped[draft["category"]].append(draft)

    lines: list[str] = []
    news_number = 1
    title_rows: list[tuple[str, int, str]] = []
    article_sections: list[str] = []

    for section_index, category in enumerate(ordered_categories(grouped), start=1):
        category_drafts = grouped[category]
        article_sections.extend([f"{number_to_chinese(section_index)}、{category}", ""])
        for draft in category_drafts:
            article_sections.extend(
                render_article(news_number, draft, summary_only=summary_only)
            )
            article_sections.append("")
            title_rows.append((category, news_number, draft["short_title"]))
            news_number += 1

    lines.extend(render_title_overview(title_rows, run_date))
    lines.extend(["", f"{format_chinese_date(run_date)}尼日利亚新闻：", ""])
    lines.extend(article_sections)
    return "\n".join(lines).rstrip() + "\n"


def ordered_categories(grouped: dict[str, list[dict[str, Any]]]) -> list[str]:
    ordered = [category for category in FIXED_CATEGORY_ORDER if category in grouped]
    extras = [category for category in grouped if category not in FIXED_CATEGORY_ORDER]
    return ordered + extras


def render_article(
    index: int,
    draft: dict[str, Any],
    *,
    summary_only: bool = False,
) -> list[str]:
    lines = [f"{index}.\u00a0{draft['body']}", ""]
    if not summary_only and draft.get("interpretation"):
        lines.append(f"解读：{draft['interpretation']}")
    if not summary_only and draft.get("note"):
        lines.append(f"背景补充：{draft['note']}")
    if draft["url"]:
        lines.append(draft["url"])
    return lines


def render_title_overview(title_rows: list[tuple[str, int, str]], run_date: str | None = None) -> list[str]:
    today = format_dot_date(run_date)
    lines = [f"今日新闻标题速览 ({today})", ""]
    grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for category, index, title in title_rows:
        grouped[category].append((index, title))

    for section_index, category in enumerate(ordered_title_categories(grouped), start=1):
        rows = grouped[category]
        lines.extend([f"{number_to_chinese(section_index)}、{category}", ""])
        for index, title in rows:
            lines.append(f"{index}.\u00a0{title}")
        lines.append("")

    return lines


def ordered_title_categories(grouped: dict[str, list[tuple[int, str]]]) -> list[str]:
    ordered = [category for category in FIXED_CATEGORY_ORDER if category in grouped]
    extras = [category for category in grouped if category not in FIXED_CATEGORY_ORDER]
    return ordered + extras


def number_to_chinese(number: int) -> str:
    numerals = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    if 1 <= number <= 10:
        return numerals[number]
    return str(number)


def format_chinese_date(run_date: str | None = None) -> str:
    date_value = parse_run_date(run_date)
    return f"{date_value.year}年{date_value.month}月{date_value.day}日"


def format_dot_date(run_date: str | None = None) -> str:
    return parse_run_date(run_date).strftime("%Y.%m.%d")


def parse_run_date(run_date: str | None = None) -> datetime:
    if run_date:
        try:
            return datetime.strptime(run_date, "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now()


def build_run_metadata(
    config: dict[str, Any], items: list[dict[str, Any]], drafts: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": config["provider"],
        "model": config["model"],
        "api_mode": config["api_mode"],
        "max_output_tokens": config["max_output_tokens"],
        "reasoning_effort": config["reasoning_effort"],
        "input_items": len(items),
        "draft_items": len(drafts),
    }


if __name__ == "__main__":
    main()
