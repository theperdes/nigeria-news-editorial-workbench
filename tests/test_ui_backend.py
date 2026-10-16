from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from ui_backend import (  # noqa: E402
    backup_before_collection_rerun,
    bulk_exclude_unreviewed,
    format_digest,
    read_review_items,
    update_review_item,
)


REVIEW = """# Nigeria News Review

Total articles: 2

## High (1)

### 1. First title

- Review decision: [ ] include [ ] maybe [ ] exclude
- Suggested category: Infrastructure
- Suggested title: First suggested
- Final category:
- Final title:
- Notes:

- Source: Source A
- Score: 70
- Type: project_opportunity
- Stage: financing
- Fetch: success
- URL: https://example.com/one
- Watch reason: project stage and financing signals
- Matched keywords: bridge, loan, construction
- Scoring reasons:
  - project keyword (+3)
  - financing signal (+2)
- Evidence snippets:
  - The project received approval.
  - Construction begins this year.

> This is the article excerpt used by an operator to decide whether the story should be included.

## Fetch Failed (1)

### 1. Failed title

- Review action: manual link check only; no article text is available for LLM input.

- Source: Source B
- Score: 10
- Fetch: request_failed
- URL: https://example.com/two
"""


class ReviewBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.run_dir = self.root / "data" / "runs" / "2026-07-20"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "review.md").write_text(REVIEW, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_and_update_review_item(self) -> None:
        items = read_review_items(self.run_dir / "review.md")
        self.assertEqual(2, len(items))
        self.assertTrue(items[0].reviewable)
        self.assertFalse(items[1].reviewable)
        self.assertEqual("High", items[0].level)
        self.assertEqual("project_opportunity", items[0].news_type)
        self.assertEqual("financing", items[0].stage)
        self.assertEqual(3, len(items[0].matched_keywords))
        self.assertEqual(2, len(items[0].scoring_reasons))
        self.assertEqual(2, len(items[0].evidence_snippets))
        self.assertIn("article excerpt", items[0].excerpt)

        updated = update_review_item(
            self.root,
            "2026-07-20",
            0,
            decision="include",
            final_category="Projects",
            final_title="Edited title",
            notes="Keep this",
        )
        self.assertEqual("include", updated.decision)
        self.assertEqual("Projects", updated.final_category)
        self.assertEqual("Edited title", updated.final_title)
        self.assertEqual("Keep this", updated.notes)
        self.assertTrue((self.root / "data" / "review.md").exists())

    def test_bulk_exclude_only_unreviewed(self) -> None:
        count = bulk_exclude_unreviewed(self.root, "2026-07-20")
        self.assertEqual(1, count)
        items = read_review_items(self.run_dir / "review.md")
        self.assertEqual("exclude", items[0].decision)
        self.assertFalse(items[1].reviewable)

    def test_rerun_backup_preserves_operator_files_and_clears_downstream(self) -> None:
        (self.run_dir / "llm_output.md").write_text("digest", encoding="utf-8")
        logs_dir = self.root / "logs"
        logs_dir.mkdir()
        (logs_dir / "daily_2026-07-20.log").write_text("old log", encoding="utf-8")

        backup_dir = backup_before_collection_rerun(self.root, "2026-07-20")

        self.assertEqual(REVIEW, (backup_dir / "review.md").read_text(encoding="utf-8"))
        self.assertEqual("digest", (backup_dir / "llm_output.md").read_text(encoding="utf-8"))
        self.assertEqual("old log", (backup_dir / "daily_2026-07-20.log").read_text(encoding="utf-8"))
        self.assertFalse((self.run_dir / "review.md").exists())
        self.assertFalse((self.run_dir / "llm_output.md").exists())
        self.assertFalse((logs_dir / "daily_2026-07-20.log").exists())


class DigestFormatterTests(unittest.TestCase):
    def test_removed_items_empty_sections_and_spacing_are_normalized(self) -> None:
        source = """今日新闻标题速览 (2026.07.20)

一、基础设施

1. 删除的新闻
2. 保留的基础设施新闻

二、能源

3. 删除的能源新闻

三、政策

4. 保留的政策新闻


2026年7月20日尼日利亚新闻：

一、基础设施

2. 基础设施正文。

解读：基础设施解读。
背景补充：基础设施背景。

https://example.com/two

二、能源

三、政策

4. 政策正文。
https://example.com/four
"""
        result = format_digest(source)
        self.assertNotIn("删除的新闻", result)
        self.assertNotIn("删除的能源新闻", result)
        self.assertIn("一、基础设施\n1.\u00a0保留的基础设施新闻", result)
        self.assertIn("二、政策\n2.\u00a0保留的政策新闻", result)
        self.assertNotIn("三、政策", result)
        self.assertIn(
            "1.\u00a0基础设施正文。\n\n"
            "解读：基础设施解读。\n"
            "背景补充：基础设施背景。\n"
            "https://example.com/two",
            result,
        )
        self.assertIn("2.\u00a0政策正文。\nhttps://example.com/four", result)
        self.assertTrue(result.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
