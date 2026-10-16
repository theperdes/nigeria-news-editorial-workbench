from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from news_ui import collection_commands, next_unreviewed_item_id, workflow_info  # noqa: E402
import news_ui  # noqa: E402


class WebsiteDemoTests(unittest.TestCase):
    def test_demo_mode_blocks_background_jobs_before_any_process_starts(self) -> None:
        with patch.object(news_ui, "DEMO_MODE", True):
            for kind in ("collection", "digest"):
                with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, "演示模式"):
                    news_ui.JobManager().start("2026-06-15", kind, "demo", [["python", "unused.py"]])


class ReviewNavigationTests(unittest.TestCase):
    def test_next_unreviewed_wraps_and_skips_current(self) -> None:
        items = [
            SimpleNamespace(id=1, decision="unreviewed"),
            SimpleNamespace(id=2, decision="exclude"),
            SimpleNamespace(id=3, decision="unreviewed"),
        ]
        self.assertEqual(3, next_unreviewed_item_id(items, 1))
        self.assertEqual(1, next_unreviewed_item_id(items, 3))

    def test_next_unreviewed_returns_none_when_review_is_complete(self) -> None:
        items = [
            SimpleNamespace(id=1, decision="include"),
            SimpleNamespace(id=2, decision="exclude"),
        ]
        self.assertIsNone(next_unreviewed_item_id(items, 1))


class WorkflowInfoTests(unittest.TestCase):
    def test_review_is_the_next_step_after_collection(self) -> None:
        snap = SimpleNamespace(
            collection_status="complete",
            review_status="pending",
            digest_status="pending",
            finalized_status="pending",
            unreviewed=12,
            included=0,
            maybe=0,
        )
        info = workflow_info(snap, None)
        self.assertEqual(2, info["stage"])
        self.assertIn("12", info["detail"])

    def test_finalize_is_the_next_step_after_digest(self) -> None:
        snap = SimpleNamespace(
            collection_status="complete",
            review_status="complete",
            digest_status="complete",
            finalized_status="pending",
            unreviewed=0,
            included=8,
            maybe=0,
        )
        self.assertEqual(4, workflow_info(snap, None)["stage"])


class CollectionCommandTests(unittest.TestCase):
    def test_collection_command_carries_selected_run_date(self) -> None:
        command = collection_commands("2026-07-22")[0]
        self.assertEqual(["--run-date", "2026-07-22"], command[-2:])
        self.assertEqual("scripts/collect.py", command[1])


if __name__ == "__main__":
    unittest.main()
