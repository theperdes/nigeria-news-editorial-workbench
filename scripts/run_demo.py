"""Reproduce a fictional editorial walkthrough, offline and without API keys."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if sys.version_info < (3, 10):
    raise SystemExit("The offline demo requires Python 3.10 or newer.")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_digest import parse_review
from prepare_llm_input import build_llm_item, build_source_index, is_fetch_failed, match_source_article
from ui_backend import format_digest, read_review_items, update_review_item


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_review(cases: list[dict]) -> str:
    lines = ["# Nigeria News Review — FICTIONAL DEMO", "", "## Synthetic examples", ""]
    for index, case in enumerate(cases, 1):
        lines.extend([f"### {index}. {case['title']}", ""])
        if case["fetch_status"] == "success":
            lines.append("- Review decision: [ ] include [ ] maybe [ ] exclude")
        else:
            lines.append("- Review action: manual link check only; no reliable article text.")
        lines.extend([
            f"- Suggested category: {case['category']}",
            f"- Suggested title: {case['title']}",
            "- Final category:", "- Final title:", "- Notes:",
            f"- Source: {case['source']}", f"- URL: {case['url']}",
            f"- Fetch: {case['fetch_status']}", "",
            f"> {case['article_text'] or '[No reliable article text]'}", "",
        ])
    return "\n".join(lines)


def prepare_inputs(cases: list[dict], review: str) -> list[dict]:
    sources = [{**case, "resolved_url": case["url"]} for case in cases]
    index = build_source_index(sources)
    selected = []
    for item in parse_review(review):
        if item["decision"] not in {"include", "maybe"}:
            continue
        source = match_source_article(item, index)
        if source is None:
            raise ValueError(f"No source match: {item['original_title']}")
        if is_fetch_failed(source) or not str(source.get("article_text", "")).strip():
            continue
        selected.append(build_llm_item(item, source))
    return selected


def reference_brief(cases: list[dict], run_date: str) -> str:
    year, month, day = (int(part) for part in run_date.split("-"))
    selected = [case for case in cases if case["decision"] == "include"]
    headings = ["一", "二"]
    overview = [f"今日新闻标题速览 ({year}.{month:02}.{day:02})", ""]
    body = [f"{year}年{month}月{day}日尼日利亚新闻：", ""]
    for index, case in enumerate(selected):
        section = f"{headings[index]}、{case['category']}"
        overview.extend([section, "", f"{index + 1}. {case['final_title']}", ""])
        body.extend([section, "", f"{index + 1}. {case['reference_zh']}", "", case["url"], ""])
    return "# 虚构样例 / FICTIONAL DEMO\n\n以下为预先撰写的参考编辑稿，不是真实报道或实时模型输出。\n\n" + format_digest("\n".join(overview + body))


def build_walkthrough(cases: list[dict], notice: str) -> str:
    template = (ROOT / "docs/demo-template.html").read_text(encoding="utf-8")
    # JSON is data, never executable text; escape '<' to prevent script closing.
    payload = json.dumps({"cases": cases, "notice": notice}, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace("__DEMO_DATA__", payload)


def run_demo(output: Path) -> dict:
    fixture = json.loads((ROOT / "examples/editorial_cases.json").read_text(encoding="utf-8"))
    if fixture.get("synthetic") is not True:
        raise ValueError("Only the explicitly synthetic fixture is supported.")
    cases = fixture["cases"]
    output.mkdir(parents=True, exist_ok=True)
    sandbox = output / "workspace"
    review_path = sandbox / "data" / "runs" / fixture["run_date"] / "review.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    before = render_review(cases)
    (output / "review-before.md").write_text(before, encoding="utf-8")
    review_path.write_text(before, encoding="utf-8")
    items = read_review_items(review_path)
    for item, case in zip(items, cases, strict=True):
        if item.reviewable:
            update_review_item(
                sandbox, fixture["run_date"], item.id,
                decision=case["decision"], final_category=case["category"],
                final_title=case["final_title"], notes=case["editor_note"],
            )
    reviewed = review_path.read_text(encoding="utf-8")
    (output / "review.md").write_text(reviewed, encoding="utf-8")
    inputs = prepare_inputs(cases, reviewed)
    expected = {case["url"] for case in cases if case["decision"] == "include"}
    if {item["url"] for item in inputs} != expected:
        raise AssertionError("Unexpected sources reached the drafting input.")
    write_json(output / "llm_input.json", inputs)
    (output / "brief.zh-CN.md").write_text(reference_brief(cases, fixture["run_date"]), encoding="utf-8")
    (ROOT / "docs/demo.html").write_text(build_walkthrough(cases, fixture["notice"]), encoding="utf-8")
    manifest = {
        "synthetic": True, "network_requests": 0, "llm_calls": 0,
        "case_count": len(cases), "draft_input_count": len(inputs),
        "decisions": {case["id"]: case["decision"] for case in cases},
        "reference_text": "Authored demonstration, not live model output",
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/demo")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output), ensure_ascii=False, indent=2))
    print("Open docs/demo.html in a browser to review the walkthrough.")


if __name__ == "__main__":
    main()

