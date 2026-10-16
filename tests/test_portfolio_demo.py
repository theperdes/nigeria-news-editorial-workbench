from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineDemoTests(unittest.TestCase):
    def test_demo_needs_no_network_and_excludes_noise_and_failed_fetches(self):
        demo = load_script("run_demo")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "examples").mkdir()
            (root / "docs").mkdir()
            shutil.copy2(ROOT / "examples/editorial_cases.json", root / "examples")
            shutil.copy2(ROOT / "docs/demo-template.html", root / "docs")
            with patch.object(demo, "ROOT", root), patch.object(
                socket.socket, "connect", side_effect=AssertionError("Demo must stay offline")
            ), patch.object(socket, "create_connection", side_effect=AssertionError("Demo must stay offline")):
                result = demo.run_demo(root / "output")
            selected = json.loads((root / "output/llm_input.json").read_text())
            self.assertEqual(5, result["case_count"])
            self.assertEqual(2, len(selected))
            self.assertEqual(
                {"https://example.invalid/demo/road-proposal", "https://example.invalid/demo/solar-commissioning"},
                {item["url"] for item in selected},
            )
            self.assertTrue(all(item["article_text"] for item in selected))
            brief = (root / "output/brief.zh-CN.md").read_text()
            self.assertIn("资金仍待批准", brief)
            self.assertIn("2兆瓦", brief)
            self.assertNotIn("200兆瓦", brief)
            self.assertIn("FICTIONAL DEMO", brief)
            self.assertNotIn("__DEMO_DATA__", (root / "docs/demo.html").read_text())

    def test_failed_fetch_cannot_enter_input_even_if_marked_include(self):
        demo = load_script("run_demo")
        cases = json.loads((ROOT / "examples/editorial_cases.json").read_text())["cases"]
        failed = cases[-1]
        review = (
            f"### 1. {failed['title']}\n"
            "- Review decision: [x] include [ ] maybe [ ] exclude\n"
            f"- URL: {failed['url']}\n"
        )
        self.assertEqual([], demo.prepare_inputs(cases, review))


class CollectorTests(unittest.TestCase):
    def test_first_failure_stops_downstream_steps_and_propagates_status(self):
        collector = load_script("collect")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            collector, "ROOT", Path(directory)
        ), patch("sys.argv", ["collect.py", "--run-date", "2026-06-15"]), patch.object(
            collector.subprocess, "run", return_value=SimpleNamespace(returncode=7)
        ) as run:
            self.assertEqual(7, collector.main())
            self.assertEqual(1, run.call_count)
            self.assertEqual(["--run-date", "2026-06-15"], run.call_args.args[0][-2:])


if __name__ == "__main__":
    unittest.main()
