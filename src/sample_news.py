from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample a diverse subset of news items.")
    parser.add_argument("--input-file", default="rss_prescreened_news.json")
    parser.add_argument("--output-file", default="rss_prescreened_sample_news.json")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260512)
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, args.input_file)
    items = load_json(input_path)
    sampled = diverse_sample(items, args.count, args.seed)
    output_path = save_json_with_latest(ROOT_DIR, args.run_date, args.output_file, sampled)
    print(f"Loaded {len(items)} items from {input_path}")
    print(f"Sampled {len(sampled)} diverse items")
    print(f"Saved sample to {output_path}")


def diverse_sample(items: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[group_key(item)].append(item)

    queues: list[deque[dict[str, Any]]] = []
    for key in sorted(groups):
        group_items = groups[key]
        rng.shuffle(group_items)
        queues.append(deque(group_items))
    rng.shuffle(queues)

    sampled: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    while queues and len(sampled) < count:
        next_queues: list[deque[dict[str, Any]]] = []
        for queue in queues:
            if len(sampled) >= count:
                next_queues.append(queue)
                continue
            while queue:
                item = queue.popleft()
                url = str(item.get("url") or item.get("google_news_url") or item.get("title", ""))
                if url in seen_urls:
                    continue
                sampled.append(item)
                seen_urls.add(url)
                break
            if queue:
                next_queues.append(queue)
        queues = next_queues
    return sampled


def group_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("search_group", "")),
            str(item.get("search_pool", "")),
            str(item.get("search_keyword", "")),
        ]
    )


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    main()
