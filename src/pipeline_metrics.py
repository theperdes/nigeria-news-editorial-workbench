from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_digest import parse_review
from pipeline_paths import add_run_date_arg, effective_run_date, run_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]

# Files the automated pipeline (run_daily.ps1) always produces. If one of these
# is missing when metrics runs, a stage likely crashed — that must be surfaced,
# not silently reported as a count of 0. Downstream/manual-step outputs
# (llm_input.json, llm_drafts.json, llm_output.md) and diagnostic dumps are
# intentionally excluded: they legitimately may not exist yet.
CORE_INPUTS = [
    "discovered_news.json",
    "rss_prescreened_news.json",
    "llm_prefilter_results.json",
    "llm_prefiltered_news.json",
    "raw_news.json",
    "scored_news.json",
    "deduped_news.json",
    "review.md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save daily pipeline quality and volume metrics.")
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_date = effective_run_date(args.run_date)
    metrics = build_metrics(run_date)
    output_path = save_json_with_latest(ROOT_DIR, run_date, "pipeline_metrics.json", metrics)
    print(f"Saved pipeline metrics to {output_path}")
    print(render_summary(metrics))


def build_metrics(run_date: str) -> dict[str, Any]:
    discovered = load_json_list(run_date, "discovered_news.json")
    same_day_duplicates = load_json_list(run_date, "discovery_same_day_duplicates.json")
    history_duplicates = load_json_list(run_date, "discovery_history_duplicates.json")
    rss_prescreened = load_json_list(run_date, "rss_prescreened_news.json")
    rss_skipped = load_json_list(run_date, "rss_prescreen_skipped_news.json")
    sample = load_json_list(run_date, "rss_prescreened_sample_news.json")
    llm_results = load_json_list(run_date, "llm_prefilter_results.json")
    llm_prefiltered = load_json_list(run_date, "llm_prefiltered_news.json")
    raw_news = load_json_list(run_date, "raw_news.json")
    scored = load_json_list(run_date, "scored_news.json")
    review_candidates = load_json_list(run_date, "review_candidates.json")
    if not review_candidates:
        review_candidates = load_json_list(run_date, "selected_news.json")
    deduped = load_json_list(run_date, "deduped_news.json")
    llm_input = load_json_list(run_date, "llm_input.json")
    llm_drafts = load_json_list(run_date, "llm_drafts.json")
    final_output_text = load_text(run_date, "llm_output.md")

    review_items = load_review_items(run_date)
    review_decisions = Counter(item["decision"] for item in review_items)
    llm_decisions = Counter(str(item.get("decision", "unknown")) for item in llm_results)
    llm_priorities = Counter(str(item.get("priority", "unknown")) for item in llm_results)
    fetch_statuses = Counter(str(item.get("fetch_status", "unknown")) for item in raw_news)
    review_candidate_lanes = Counter(str(item.get("selection_lane", "unknown")) for item in review_candidates)
    deduped_lanes = Counter(str(item.get("selection_lane", "unknown")) for item in deduped)
    rss_skip_reasons = Counter(str(item.get("rss_prescreen_reason", "unknown")) for item in rss_skipped)

    return {
        "run_date": run_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "missing_inputs": missing_core_inputs(run_date),
        "counts": {
            "discovered_after_rss_dedupe": len(discovered),
            "same_day_duplicates_removed": len(same_day_duplicates),
            "history_duplicates_removed": len(history_duplicates),
            "rss_prescreen_kept": len(rss_prescreened),
            "rss_prescreen_skipped": len(rss_skipped),
            "rss_sample_size": len(sample),
            "llm_prefilter_decisions": len(llm_results),
            "llm_fetch_candidates": len(llm_prefiltered),
            "raw_articles": len(raw_news),
            "scored_articles": len(scored),
            "review_candidates": len(review_candidates),
            "selected_for_review": len(review_candidates),
            "deduped_for_review": len(deduped),
            "review_items": len(review_items),
            "llm_input_items": len(llm_input),
            "llm_drafts": len(llm_drafts),
            "llm_output_chars": len(final_output_text),
        },
        "completion": {
            "review_completed": bool(review_items)
            and any(item["decision"] in {"include", "maybe", "exclude"} for item in review_items),
            "digest_generated": bool(llm_drafts),
        },
        "rates": build_rates(
            discovered,
            rss_prescreened,
            llm_results,
            llm_prefiltered,
            raw_news,
            review_candidates,
            deduped,
            review_items,
            llm_input,
        ),
        "breakdowns": {
            "llm_decisions": dict(llm_decisions),
            "llm_priorities": dict(llm_priorities),
            "fetch_statuses": dict(fetch_statuses),
            "review_candidate_lanes": dict(review_candidate_lanes),
            "selected_lanes": dict(review_candidate_lanes),
            "deduped_lanes": dict(deduped_lanes),
            "review_decisions": dict(review_decisions),
            "top_rss_skip_reasons": dict(rss_skip_reasons.most_common(10)),
        },
    }


def build_rates(
    discovered: list[dict[str, Any]],
    rss_prescreened: list[dict[str, Any]],
    llm_results: list[dict[str, Any]],
    llm_prefiltered: list[dict[str, Any]],
    raw_news: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    deduped: list[dict[str, Any]],
    review_items: list[dict[str, str]],
    llm_input: list[dict[str, Any]],
) -> dict[str, float]:
    return {
        "rss_prescreen_keep_rate": ratio(len(rss_prescreened), len(discovered)),
        "llm_fetch_rate": ratio(len(llm_prefiltered), len(llm_results)),
        "fetch_success_rate": ratio(
            count_where(raw_news, "fetch_status", "success"), len(raw_news)
        ),
        "article_to_review_selection_rate": ratio(len(selected), len(raw_news)),
        "review_dedupe_keep_rate": ratio(len(deduped), len(selected)),
        "manual_final_keep_rate": ratio(len(llm_input), len(review_items)),
    }


def load_json_list(run_date: str, filename: str) -> list[dict[str, Any]]:
    path = run_path(ROOT_DIR, run_date, filename)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, list) else []


def load_review_items(run_date: str) -> list[dict[str, str]]:
    path = run_path(ROOT_DIR, run_date, "review.md")
    if not path.exists():
        return []
    items = parse_review(path.read_text(encoding="utf-8"))
    # Only sections with an actual "- Review decision:" checkbox are real review
    # candidates. Fetch Failed / Below Threshold sections reuse the same
    # "### N." heading but have no checkbox, and must not inflate review_items
    # (which would also deflate manual_final_keep_rate).
    return [item for item in items if item.get("has_decision")]


def missing_core_inputs(run_date: str) -> list[str]:
    missing = [
        name
        for name in CORE_INPUTS
        if not run_path(ROOT_DIR, run_date, name).exists()
    ]
    # review_candidates.json has a legacy fallback name; only flag it missing
    # when neither file exists.
    if (
        not run_path(ROOT_DIR, run_date, "review_candidates.json").exists()
        and not run_path(ROOT_DIR, run_date, "selected_news.json").exists()
    ):
        missing.append("review_candidates.json")
    return missing


def load_text(run_date: str, filename: str) -> str:
    path = run_path(ROOT_DIR, run_date, filename)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def count_where(items: list[dict[str, Any]], key: str, value: str) -> int:
    return sum(1 for item in items if item.get(key) == value)


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def render_summary(metrics: dict[str, Any]) -> str:
    counts = metrics["counts"]
    rates = metrics["rates"]
    summary = (
        f"RSS {counts['discovered_after_rss_dedupe']} -> "
        f"prescreen {counts['rss_prescreen_kept']} "
        f"({rates['rss_prescreen_keep_rate']:.1%}) -> "
        f"LLM fetch {counts['llm_fetch_candidates']} "
        f"({rates['llm_fetch_rate']:.1%}) -> "
        f"review {counts['deduped_for_review']} -> "
        f"final input {counts['llm_input_items']} "
        f"({rates['manual_final_keep_rate']:.1%})"
    )
    missing = metrics.get("missing_inputs")
    if missing:
        summary += f"\nWARN missing expected files (a stage may have failed): {', '.join(missing)}"
    return summary


if __name__ == "__main__":
    main()
