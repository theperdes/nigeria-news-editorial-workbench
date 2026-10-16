"""Explicit, cross-platform live collection runner. Creates no scheduled job."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STEPS = (
    ("discover", "src/discover.py", []),
    ("rss_prescreen", "src/rss_prescreen.py", []),
    ("llm_prefilter", "src/llm_prefilter.py", []),
    ("llm_dedupe_prefiltered", "src/llm_dedupe_prefiltered.py", []),
    ("fetch_articles", "src/fetch_articles.py", ["--workers", "6"]),
    ("filter", "src/filter.py", []),
    ("dedupe", "src/dedupe.py", []),
    ("export_review", "src/export_review.py", []),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", default=date.today().isoformat())
    args = parser.parse_args()
    try:
        datetime.strptime(args.run_date, "%Y-%m-%d")
    except ValueError:
        parser.error("--run-date must be YYYY-MM-DD")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1")
    with (log_dir / f"daily_{args.run_date}.log").open("a", encoding="utf-8") as log:
        for label, script, extra in STEPS:
            print(f"START {label}", flush=True)
            log.write(f"START {label}\n")
            log.flush()
            result = subprocess.run(
                [sys.executable, script, *extra, "--run-date", args.run_date],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            if result.returncode:
                print(f"FAIL {label} (exit {result.returncode}); stopped. See logs.", flush=True)
                return result.returncode
            print(f"DONE {label}", flush=True)
        log.write("Daily pipeline finished OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

