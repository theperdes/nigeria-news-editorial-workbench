"""Open the original NiceGUI website with isolated fictional demo records."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    # Set before importing the actual website; its layout and registered pages
    # are reused. Demo mode blocks background collection and generation jobs.
    os.environ["NEWS_UI_DEMO"] = "1"
    import news_ui as website
    from nicegui import ui
    from run_demo import reference_brief, render_review
    from ui_backend import read_review_items, update_review_item

    fixture = json.loads((ROOT / "examples/editorial_cases.json").read_text(encoding="utf-8"))
    if fixture.get("synthetic") is not True:
        raise ValueError("The website demo requires the synthetic fixture.")
    with tempfile.TemporaryDirectory(prefix="news-workbench-demo-") as directory:
        sandbox = Path(directory)
        website.ROOT_DIR = sandbox
        run_date = website.current_run_date()
        run_dir = sandbox / "data" / "runs" / run_date
        run_dir.mkdir(parents=True)
        cases = fixture["cases"]
        path = run_dir / "review.md"
        path.write_text(render_review(cases), encoding="utf-8")
        for item, case in zip(read_review_items(path), cases, strict=True):
            if item.reviewable:
                update_review_item(
                    sandbox, run_date, item.id, decision=case["decision"],
                    final_title=case["final_title"], final_category=case["category"],
                    notes=case["editor_note"],
                )
        (run_dir / "llm_output.md").write_text(reference_brief(cases, run_date), encoding="utf-8")
        # No artificial discovery/fetch counters: those steps were not run.
        ui.run(
            host="127.0.0.1", port=args.port, reload=False, show=False,
            title="Nigeria News · 原网站虚构数据演示", favicon="📰",
            storage_secret=secrets.token_urlsafe(32),
        )


if __name__ == "__main__":
    main()
