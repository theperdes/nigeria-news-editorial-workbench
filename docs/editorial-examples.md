# Editorial examples / 编辑案例

All people, organizations, events and figures in these examples are fictional. They demonstrate editing decisions, not real news, professional advice or live model performance. The draft and reference versions were written for the walkthrough.

## 1. Preserve a proposal's uncertainty

**Fictional source:** “The fictional Meadow Works Authority proposed rehabilitating an 18 km road at an estimated cost of NGN 3.2 billion, subject to funding approval. No contractor has been appointed. Expressions of interest would close on 30 June 2026 if the process proceeds.”

**Illustrative draft to correct:** “草甸工程局已批准32亿奈拉道路合同，将修建18公里新公路，承包商月底进场。”

**Reference edit:** “虚构的草甸工程局拟以约32亿奈拉修复18公里公路，资金仍待批准，承包商尚未确定。若程序推进，意向征集将于2026年6月30日截止。”

The edit restores *proposed*, *rehabilitating* and *subject to funding approval*, removes an invented contractor mobilization, and distinguishes an expression-of-interest deadline from a construction start date. NGN 3.2 billion is rendered as 32亿奈拉.

## 2. Do not turn commissioning into a price promise

**Fictional source:** “The fictional Sunfield Energy Cooperative commissioned a 2 MW solar mini-grid on 12 June 2026. The announcement did not disclose the tariff, financing amount or number of connected households.”

**Illustrative draft to correct:** “阳田能源合作社计划建设200兆瓦电站，将为数千户家庭大幅降低电费。”

**Reference edit:** “虚构的阳田能源合作社于2026年6月12日投运一座2兆瓦光伏微电网；公告未披露电价、融资额或接入户数。”

The edit fixes 2 MW, preserves the completed commissioning stage, and removes unsupported household and tariff claims.

## 3. Repeat reporting

A second fictional outlet repeats the first road proposal without new facts. The reference decision is `exclude`, with a note pointing back to the primary case. This demo decision is explicitly authored; it is not a claimed result of running the full semantic deduplication pipeline.

## 4. Promotional material

A fictional conference announcement promises “transformative opportunities” but supplies no decision, budget, implementation party or delivery date. It is excluded from this example brief. A low-information item is not made useful by translating it at length.

## 5. No reliable article text

A final item has a failed-fetch status and no body text. The walkthrough labels it for manual source checking and leaves it out of drafting input. The headline alone is insufficient to support a summary.

## What the demo actually runs

`scripts/run_demo.py` uses `ui_backend.read_review_items` and `update_review_item`, `build_digest.parse_review`, the source matching and item preparation functions in `prepare_llm_input`, and `ui_backend.format_digest`. It checks that only the two selected, successfully fetched cases reach the prepared input. Chinese reference text is supplied by the fixture and formatted locally.

The full repository also includes discovery, screening, scoring, deduplication and optional AI drafting. These examples do not claim to run or benchmark those network-dependent stages.

