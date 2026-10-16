from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]


class SearchConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.google_news = yaml.safe_load(
            (ROOT_DIR / "config.yaml").read_text(encoding="utf-8")
        )["google_news"]

    def test_core_queries_are_unique_and_tinubu_is_not_duplicated(self) -> None:
        keywords = self.google_news["core"]["keywords"]
        normalized = [str(keyword).strip().casefold() for keyword in keywords]
        self.assertEqual(len(normalized), len(set(normalized)))
        self.assertEqual(
            [keyword for keyword in normalized if "tinubu" in keyword],
            ["tinubu"],
        )

    def test_scheduled_pools_exist_and_fit_the_per_pool_limit(self) -> None:
        rotation = self.google_news["rotation"]
        pools = rotation["pools"]
        daily_limit = int(rotation["daily_limit"])

        for weekday, pool_names in rotation["schedule"].items():
            with self.subTest(weekday=weekday):
                for pool_name in pool_names:
                    self.assertIn(pool_name, pools)
                    self.assertLessEqual(
                        len(pools[pool_name]["keywords"]),
                        daily_limit,
                        msg=f"{pool_name} contains keywords that would never run",
                    )

    def test_scheduled_topics_cover_editorial_subjects(self) -> None:
        rotation = self.google_news["rotation"]
        scheduled = {name for names in rotation["schedule"].values() for name in names}
        self.assertTrue({"road_bridge", "multilateral_finance", "power_detail"} <= scheduled)
        self.assertFalse(any("companies" in name for name in scheduled))


if __name__ == "__main__":
    unittest.main()
