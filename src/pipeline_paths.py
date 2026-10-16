from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def add_run_date_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-date",
        default=None,
        help="Pipeline run date in YYYY-MM-DD format. Defaults to today.",
    )


def run_data_dir(root_dir: Path, run_date: str) -> Path:
    return root_dir / "data" / "runs" / run_date


def run_path(root_dir: Path, run_date: str, filename: str) -> Path:
    return run_data_dir(root_dir, run_date) / filename


def latest_path(root_dir: Path, filename: str) -> Path:
    return root_dir / "data" / filename


def resolve_input_path(root_dir: Path, run_date: str, filename: str) -> Path:
    dated_path = run_path(root_dir, run_date, filename)
    if dated_path.exists():
        return dated_path
    return latest_path(root_dir, filename)


def effective_run_date(run_date: str | None) -> str:
    return run_date or datetime.now().strftime("%Y-%m-%d")


def resolve_pipeline_input_path(
    root_dir: Path,
    run_date: str | None,
    filename: str,
    *,
    allow_latest_fallback: bool | None = None,
) -> Path:
    actual_run_date = effective_run_date(run_date)
    dated_path = run_path(root_dir, actual_run_date, filename)
    if dated_path.exists():
        return dated_path

    should_fallback = allow_latest_fallback if allow_latest_fallback is not None else run_date is None
    if should_fallback:
        return latest_path(root_dir, filename)

    raise FileNotFoundError(
        f"Expected {filename} in run directory for --run-date {actual_run_date}: {dated_path}"
    )


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to ``path`` atomically.

    The content is written to a temp file in the same directory and then
    ``os.replace``-d into place (an atomic rename on Windows and POSIX). A
    crash mid-write leaves the previous file intact, never a truncated /
    half-written file that the next pipeline step would fail to parse.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def save_json_with_latest(root_dir: Path, run_date: str, filename: str, data: Any) -> Path:
    run_date = effective_run_date(run_date)
    dated_path = run_path(root_dir, run_date, filename)
    atomic_write_text(dated_path, json.dumps(data, ensure_ascii=False, indent=2))
    sync_latest(root_dir, dated_path, filename)
    return dated_path


def write_text_with_latest(root_dir: Path, run_date: str, filename: str, text: str) -> Path:
    run_date = effective_run_date(run_date)
    dated_path = run_path(root_dir, run_date, filename)
    atomic_write_text(dated_path, text)
    sync_latest(root_dir, dated_path, filename)
    return dated_path


def sync_latest(root_dir: Path, dated_path: Path, filename: str) -> None:
    target = latest_path(root_dir, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_name(f"{target.name}.tmp")
    shutil.copyfile(dated_path, tmp_target)
    os.replace(tmp_target, target)
