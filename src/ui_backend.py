from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline_paths import atomic_write_text, run_path, sync_latest


COUNT_FILES = {
    "discovered": "discovered_news.json",
    "prescreened": "rss_prescreened_news.json",
    "prefiltered": "llm_prefiltered_news.json",
    "fetched": "raw_news.json",
    "review": "review_candidates.json",
}

DECISION_PATTERN = re.compile(
    r"^- Review decision:\s*\[(?P<include>[ xX])\]\s*include\s*"
    r"\[(?P<maybe>[ xX])\]\s*maybe\s*"
    r"\[(?P<exclude>[ xX])\]\s*exclude\s*$",
    re.MULTILINE,
)
ARTICLE_PATTERN = re.compile(
    r"^###\s+(?P<number>\d+)\.\s+(?P<title>.*?)\r?\n"
    r"(?P<body>.*?)(?=^###\s+\d+\.\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
FIELD_NAMES = {
    "suggested_category": "Suggested category",
    "suggested_title": "Suggested title",
    "final_category": "Final category",
    "final_title": "Final title",
    "notes": "Notes",
    "source": "Source",
    "score": "Score",
    "url": "URL",
    "fetch": "Fetch",
    "news_type": "Type",
    "stage": "Stage",
    "watch_reason": "Watch reason",
    "matched_keywords": "Matched keywords",
    "duplicates_collapsed": "Duplicates collapsed",
    "duplicate_sources": "Duplicate sources",
    "cross_day_duplicate": "Cross-day possible duplicate",
}
CHINESE_NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
SECTION_PATTERN = re.compile(r"^([一二三四五六七八九十]+)、(.+)$", re.MULTILINE)
NUMBERED_LINE_PATTERN = re.compile(r"^(\d+)[.．、]\s*(.+)$", re.MULTILINE)
RERUN_RESET_FILES = [
    "review.md",
    "llm_input.json",
    "llm_drafts.json",
    "llm_output.md",
    "llm_run.json",
    "pipeline_metrics.json",
    "ui_state.json",
]


@dataclass
class ReviewItem:
    id: int
    number: int
    level: str
    title: str
    decision: str
    reviewable: bool
    suggested_category: str
    suggested_title: str
    final_category: str
    final_title: str
    notes: str
    source: str
    score: str
    url: str
    fetch: str
    news_type: str
    stage: str
    watch_reason: str
    matched_keywords: list[str]
    scoring_reasons: list[str]
    evidence_snippets: list[str]
    excerpt: str
    duplicates_collapsed: str
    duplicate_sources: str
    cross_day_duplicate: str

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "level": self.level,
            "decision": self.decision,
            "source": self.source,
            "score": self.score,
            "display_title": self.final_title or self.suggested_title or self.title,
            "display_category": self.final_category or self.suggested_category,
        }


@dataclass
class RunSnapshot:
    run_date: str
    counts: dict[str, int]
    collection_status: str
    review_status: str
    digest_status: str
    finalized_status: str
    reviewable: int
    included: int
    maybe: int
    excluded: int
    unreviewed: int
    review_exists: bool
    digest_exists: bool


def available_run_dates(root_dir: Path) -> list[str]:
    runs_dir = root_dir / "data" / "runs"
    if not runs_dir.exists():
        return []
    dates = [path.name for path in runs_dir.iterdir() if path.is_dir() and is_run_date(path.name)]
    return sorted(dates, reverse=True)


def backup_before_collection_rerun(root_dir: Path, run_date: str) -> Path:
    """Back up operator work and clear downstream outputs before re-collection."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = root_dir / "data" / "run_backups" / run_date / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)

    dated_dir = root_dir / "data" / "runs" / run_date
    for filename in RERUN_RESET_FILES:
        source = dated_dir / filename
        if source.exists():
            shutil.copy2(source, backup_dir / filename)
            source.unlink()

    log_path = root_dir / "logs" / f"daily_{run_date}.log"
    if log_path.exists():
        shutil.copy2(log_path, backup_dir / log_path.name)
        log_path.unlink()

    return backup_dir


def is_run_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def load_json_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("items", "articles", "results"):
            if isinstance(data.get(key), list):
                return len(data[key])
    return 0


def read_review_items(path: Path) -> list[ReviewItem]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    items: list[ReviewItem] = []
    for item_id, match in enumerate(ARTICLE_PATTERN.finditer(text)):
        body = match.group("body")
        decision_match = DECISION_PATTERN.search(body)
        reviewable = decision_match is not None
        items.append(
            ReviewItem(
                id=item_id,
                number=int(match.group("number")),
                level=section_before(text, match.start()),
                title=match.group("title").strip(),
                decision=parse_decision(decision_match),
                reviewable=reviewable,
                suggested_category=field_value(body, "suggested_category"),
                suggested_title=field_value(body, "suggested_title"),
                final_category=field_value(body, "final_category"),
                final_title=field_value(body, "final_title"),
                notes=field_value(body, "notes"),
                source=field_value(body, "source"),
                score=field_value(body, "score"),
                url=field_value(body, "url"),
                fetch=field_value(body, "fetch"),
                news_type=field_value(body, "news_type"),
                stage=field_value(body, "stage"),
                watch_reason=field_value(body, "watch_reason"),
                matched_keywords=comma_values(field_value(body, "matched_keywords")),
                scoring_reasons=nested_values(body, "Scoring reasons"),
                evidence_snippets=nested_values(body, "Evidence snippets"),
                excerpt=blockquote_text(body),
                duplicates_collapsed=field_value(body, "duplicates_collapsed"),
                duplicate_sources=field_value(body, "duplicate_sources"),
                cross_day_duplicate=field_value(body, "cross_day_duplicate"),
            )
        )
    return items


def section_before(text: str, position: int) -> str:
    headings = list(re.finditer(r"^##\s+(.+?)(?:\s+\(\d+\))?\s*$", text[:position], re.MULTILINE))
    return headings[-1].group(1).strip() if headings else "Other"


def field_value(body: str, field: str) -> str:
    label = re.escape(FIELD_NAMES[field])
    match = re.search(rf"^- {label}:[ \t]*(.*)$", body, re.MULTILINE)
    return match.group(1).strip() if match else ""


def comma_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def nested_values(body: str, label: str) -> list[str]:
    match = re.search(
        rf"^- {re.escape(label)}:[ \t]*\n(?P<items>(?:[ \t]+- .*\n?)*)",
        body,
        re.MULTILINE,
    )
    if not match:
        return []
    return [
        re.sub(r"^[ \t]+-\s*", "", line).strip()
        for line in match.group("items").splitlines()
        if line.strip()
    ]


def blockquote_text(body: str) -> str:
    lines = [line[2:].strip() for line in body.splitlines() if line.startswith("> ")]
    return "\n".join(lines)


def parse_decision(match: re.Match[str] | None) -> str:
    if not match:
        return "unreviewed"
    for decision in ("include", "maybe", "exclude"):
        if match.group(decision).strip().casefold() == "x":
            return decision
    return "unreviewed"


def update_review_item(
    root_dir: Path,
    run_date: str,
    item_id: int,
    *,
    decision: str | None = None,
    final_category: str | None = None,
    final_title: str | None = None,
    notes: str | None = None,
) -> ReviewItem:
    path = run_path(root_dir, run_date, "review.md")
    text = path.read_text(encoding="utf-8-sig")
    matches = list(ARTICLE_PATTERN.finditer(text))
    if item_id < 0 or item_id >= len(matches):
        raise IndexError(f"Review item {item_id} does not exist")

    match = matches[item_id]
    block = match.group(0)
    if decision is not None:
        if decision not in {"include", "maybe", "exclude", "unreviewed"}:
            raise ValueError(f"Unsupported review decision: {decision}")
        block = DECISION_PATTERN.sub(decision_line(decision), block, count=1)
    if final_category is not None:
        block = replace_field(block, "final_category", final_category)
    if final_title is not None:
        block = replace_field(block, "final_title", final_title)
    if notes is not None:
        block = replace_field(block, "notes", notes)

    updated = text[: match.start()] + block + text[match.end() :]
    atomic_write_text(path, updated)
    sync_latest(root_dir, path, "review.md")
    return read_review_items(path)[item_id]


def bulk_exclude_unreviewed(root_dir: Path, run_date: str) -> int:
    path = run_path(root_dir, run_date, "review.md")
    text = path.read_text(encoding="utf-8-sig")
    replacements = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacements
        if parse_decision(match) == "unreviewed":
            replacements += 1
            return decision_line("exclude")
        return match.group(0)

    updated = DECISION_PATTERN.sub(replace, text)
    atomic_write_text(path, updated)
    sync_latest(root_dir, path, "review.md")
    return replacements


def decision_line(decision: str) -> str:
    marks = {name: "x" if name == decision else " " for name in ("include", "maybe", "exclude")}
    return (
        f"- Review decision: [{marks['include']}] include "
        f"[{marks['maybe']}] maybe [{marks['exclude']}] exclude"
    )


def replace_field(block: str, field: str, value: str) -> str:
    label = FIELD_NAMES[field]
    clean_value = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    pattern = re.compile(rf"^- {re.escape(label)}:[ \t]*.*$", re.MULTILINE)
    replacement = f"- {label}: {clean_value}"
    if pattern.search(block):
        return pattern.sub(replacement, block, count=1)
    return block


def snapshot(root_dir: Path, run_date: str) -> RunSnapshot:
    counts = {
        key: load_json_count(run_path(root_dir, run_date, filename))
        for key, filename in COUNT_FILES.items()
    }
    review_path = run_path(root_dir, run_date, "review.md")
    digest_path = run_path(root_dir, run_date, "llm_output.md")
    items = read_review_items(review_path)
    reviewable_items = [item for item in items if item.reviewable]
    decisions = {name: 0 for name in ("include", "maybe", "exclude", "unreviewed")}
    for item in reviewable_items:
        decisions[item.decision] += 1

    log_path = root_dir / "logs" / f"daily_{run_date}.log"
    log_text = read_text(log_path)
    review_exists = review_path.exists()
    collection_done = "Daily pipeline finished OK" in log_text or review_exists
    if collection_done:
        collection_status = "complete"
    elif "Daily pipeline finished with failures" in log_text:
        collection_status = "error"
    elif "Daily pipeline started" in log_text:
        collection_status = "running"
    else:
        collection_status = "pending"

    if not review_exists:
        review_status = "pending"
    elif reviewable_items and decisions["unreviewed"] == 0:
        review_status = "complete"
    else:
        review_status = "running"

    digest_exists = digest_path.exists() and digest_path.stat().st_size > 0
    digest_status = "complete" if digest_exists else "pending"
    if digest_exists and digest_is_finalized(root_dir, run_date):
        finalized_status = "complete"
    elif digest_exists:
        finalized_status = "unknown"
    else:
        finalized_status = "pending"
    return RunSnapshot(
        run_date=run_date,
        counts=counts,
        collection_status=collection_status,
        review_status=review_status,
        digest_status=digest_status,
        finalized_status=finalized_status,
        reviewable=len(reviewable_items),
        included=decisions["include"],
        maybe=decisions["maybe"],
        excluded=decisions["exclude"],
        unreviewed=decisions["unreviewed"],
        review_exists=review_exists,
        digest_exists=digest_exists,
    )


def read_digest(root_dir: Path, run_date: str) -> str:
    return read_text(run_path(root_dir, run_date, "llm_output.md"))


def save_digest(root_dir: Path, run_date: str, text: str) -> Path:
    path = run_path(root_dir, run_date, "llm_output.md")
    atomic_write_text(path, text.rstrip() + "\n")
    sync_latest(root_dir, path, "llm_output.md")
    clear_finalized(root_dir, run_date)
    return path


def finalize_digest(root_dir: Path, run_date: str, text: str | None = None) -> str:
    source = text if text is not None else read_digest(root_dir, run_date)
    formatted = format_digest(source)
    path = run_path(root_dir, run_date, "llm_output.md")
    atomic_write_text(path, formatted)
    sync_latest(root_dir, path, "llm_output.md")
    write_ui_state(root_dir, run_date, {"digest_finalized_hash": text_hash(formatted)})
    return formatted


def format_digest(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    body_heading_match = re.search(r"(?m)^20\d{2}年\d{1,2}月\d{1,2}日.*新闻：\s*$", normalized)
    if not body_heading_match:
        raise ValueError("未找到“YYYY年M月D日…新闻：”正文标题，无法自动定稿。")

    overview_text = normalized[: body_heading_match.start()].strip()
    body_heading = body_heading_match.group(0).strip()
    body_text = normalized[body_heading_match.end() :].strip()
    overview_titles = parse_overview_titles(overview_text)
    body_sections = parse_body_sections(body_text)
    body_sections = [(title, items) for title, items in body_sections if items]
    if not body_sections:
        raise ValueError("正文中没有找到编号新闻条目。")

    overview_title = next((line.strip() for line in overview_text.splitlines() if line.strip()), "今日新闻标题速览")
    overview_parts = [overview_title, ""]
    body_parts = [body_heading, ""]
    next_number = 1

    for section_index, (section_title, items) in enumerate(body_sections):
        numeral = CHINESE_NUMERALS[section_index] if section_index < len(CHINESE_NUMERALS) else str(section_index + 1)
        heading = f"{numeral}、{section_title}"
        overview_parts.append(heading)
        body_parts.extend([heading, ""])

        for item_index, (old_number, content) in enumerate(items):
            title = overview_titles.get(old_number) or derive_title(content)
            overview_parts.append(f"{next_number}.\u00a0{title}")
            body_parts.append(f"{next_number}.\u00a0{format_article_content(content)}")
            next_number += 1
            if item_index < len(items) - 1:
                body_parts.append("")

        if section_index < len(body_sections) - 1:
            overview_parts.append("")
            body_parts.append("")

    return "\n".join(overview_parts).rstrip() + "\n\n\n" + "\n".join(body_parts).rstrip() + "\n"


def parse_overview_titles(text: str) -> dict[int, str]:
    return {int(match.group(1)): match.group(2).strip() for match in NUMBERED_LINE_PATTERN.finditer(text)}


def parse_body_sections(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    headings = list(SECTION_PATTERN.finditer(text))
    sections: list[tuple[str, list[tuple[int, str]]]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section_text = text[heading.end() : end].strip()
        item_matches = list(NUMBERED_LINE_PATTERN.finditer(section_text))
        items: list[tuple[int, str]] = []
        for item_index, item_match in enumerate(item_matches):
            item_end = item_matches[item_index + 1].start() if item_index + 1 < len(item_matches) else len(section_text)
            content = section_text[item_match.start(2) : item_end].strip()
            items.append((int(item_match.group(1)), content))
        sections.append((heading.group(2).strip(), items))
    return sections


def format_article_content(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    ancillary_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith(("解读：", "背景补充：", "注：", "http://", "https://"))
        ),
        None,
    )
    if ancillary_index is None:
        return "\n".join(lines)
    main_lines = lines[:ancillary_index]
    ancillary_lines = lines[ancillary_index:]
    if not main_lines:
        return "\n".join(ancillary_lines)
    separator = "\n\n" if ancillary_lines[0].startswith("解读：") else "\n"
    return "\n".join(main_lines) + separator + "\n".join(ancillary_lines)


def derive_title(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "未命名新闻")
    first_sentence = re.split(r"[。！？]", first_line, maxsplit=1)[0]
    return first_sentence[:42].rstrip() + ("…" if len(first_sentence) > 42 else "")


def read_ui_state(root_dir: Path, run_date: str) -> dict[str, Any]:
    path = run_path(root_dir, run_date, "ui_state.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_ui_state(root_dir: Path, run_date: str, updates: dict[str, Any]) -> None:
    state = read_ui_state(root_dir, run_date)
    state.update(updates)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = run_path(root_dir, run_date, "ui_state.json")
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def clear_finalized(root_dir: Path, run_date: str) -> None:
    state = read_ui_state(root_dir, run_date)
    if "digest_finalized_hash" not in state:
        return
    write_ui_state(root_dir, run_date, {"digest_finalized_hash": None})


def digest_is_finalized(root_dir: Path, run_date: str) -> bool:
    text = read_digest(root_dir, run_date)
    expected = read_ui_state(root_dir, run_date).get("digest_finalized_hash")
    return bool(text and expected and text_hash(text) == expected)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
