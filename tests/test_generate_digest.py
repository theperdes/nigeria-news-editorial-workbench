from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from generate_digest import (  # noqa: E402
    build_article_prompt,
    normalize_draft,
    render_digest,
)


class DigestDraftTests(unittest.TestCase):
    def test_normalize_draft_preserves_interpretation_and_note(self) -> None:
        draft = normalize_draft(
            {
                "category": "基础设施与项目动态",
                "short_title": "州政府批准公路项目",
                "body": "州政府批准建设一条公路。",
                "interpretation": "项目已有明确决策，但尚未披露资金和承包商。",
                "note": "该项目仍处于前期准备阶段。",
                "url": "https://example.com/story?utm_source=openai",
            }
        )

        self.assertEqual(
            "项目已有明确决策，但尚未披露资金和承包商。",
            draft["interpretation"],
        )
        self.assertEqual("该项目仍处于前期准备阶段。", draft["note"])
        self.assertEqual("https://example.com/story", draft["url"])

    def test_normalize_old_summary_only_draft_is_backward_compatible(self) -> None:
        draft = normalize_draft(
            {
                "category": "政策、监管与财政预算",
                "short_title": "政府公布新预算",
                "body": "政府公布新预算。",
                "url": "https://example.com/budget",
            }
        )

        self.assertEqual("", draft["interpretation"])
        self.assertEqual("", draft["note"])

    def test_render_digest_includes_interpretation_and_background(self) -> None:
        output = render_digest(
            [
                {
                    "category": "基础设施与项目动态",
                    "short_title": "州政府批准公路项目",
                    "body": "州政府批准建设一条公路。",
                    "interpretation": "项目已有决策，但资金尚不明确。",
                    "note": "公开信息尚未披露承包商。",
                    "url": "https://example.com/story",
                }
            ],
            "2026-07-30",
        )

        self.assertIn("解读：项目已有决策，但资金尚不明确。", output)
        self.assertIn("背景补充：公开信息尚未披露承包商。", output)

    def test_summary_only_suppresses_interpretation_and_background(self) -> None:
        output = render_digest(
            [
                {
                    "category": "基础设施与项目动态",
                    "short_title": "州政府批准公路项目",
                    "body": "州政府批准建设一条公路。",
                    "interpretation": "项目已有决策。",
                    "note": "背景信息。",
                    "url": "https://example.com/story",
                }
            ],
            "2026-07-30",
            summary_only=True,
        )

        self.assertNotIn("解读：", output)
        self.assertNotIn("背景补充：", output)

    def test_prompt_uses_current_analysis_requirements(self) -> None:
        template = (ROOT_DIR / "prompts" / "article_draft.md").read_text(encoding="utf-8")
        prompt = build_article_prompt(
            template,
            {"article_text": "正文", "url": "https://example.com/story"},
            {"max_article_text_chars": 9000},
        )

        self.assertIn('"interpretation": "新闻解读一段，约100字"', prompt)
        self.assertIn("不强行引申项目机会", prompt)
        self.assertIn('"article_text": "正文"', prompt)


if __name__ == "__main__":
    unittest.main()
