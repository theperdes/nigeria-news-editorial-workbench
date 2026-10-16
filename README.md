# Nigeria News Editorial Workbench

**English sources → editorial review → a concise Chinese brief.**

A personal project by **Zhiqi Fang**, developed independently with AI-assisted coding. It combines a Python news pipeline with a local review workbench. The aim is to make source checking, selection and Chinese editing easier to follow, while keeping an editor responsible for the final text.

[中文介绍](README.zh-CN.md) · [Editorial examples](docs/editorial-examples.md) · [Running the workbench](docs/usage.md)

![Original NiceGUI website, using fictional demo records](docs/images/original-workbench-review.jpg)

## Preview the original website

The website is implemented in **`src/news_ui.py`**. Its four working areas are the dashboard, news review, digest editing/finalization and run history. The original layouts remain in this edition.

After installing `requirements.txt` in a virtual environment, launch the same website with isolated fictional data and no live AI calls:

```sh
python scripts/preview_workbench.py
```

Open `http://127.0.0.1:8791/`. Review decisions, title/category/notes editing and brief formatting use the real website code. Demo records live in a temporary directory; collection and generation jobs are blocked in demo mode. The original alternate layouts remain at `/classic`, `/preview-c` and `/preview-c2`.

## Supplementary editorial walkthrough

Open **[the offline walkthrough](docs/demo.html)** in a browser after downloading the repository. On GitHub, use the screenshot above or read the [editorial examples](docs/editorial-examples.md).

To reproduce the walkthrough with **Python 3.10+ and the standard library only**:

```sh
python scripts/run_demo.py
```

The demo uses five short, fictional English items. It exercises the project's review parsing, decision updates, input preparation and Chinese brief formatting. It writes review files, selected inputs and a brief to `output/demo/`, and rebuilds `docs/demo.html`. It does **not** fetch news, read `.env`, call an AI service or use employer/client records. Chinese drafts and reference edits are authored examples, not the results of a live model run.

## Why I built it

Reading English news is only part of producing a useful Chinese brief. Repeated reports, uncertain project stages, distracting text and inconsistent names all create editing work. A headline saying a project is *proposed* must not become a Chinese summary saying it has been *awarded*.

I built this workflow around those decisions: keep a traceable source, make uncertainty visible, group likely duplicates, and leave selection and wording under human control.

## What the full workflow does

1. Discover English news through RSS queries and configurable topics.
2. Apply rule-based screening and optional LLM screening before fetching full text.
3. Score and group candidates, with duplicate and fetch-failure signals.
4. Let an editor include, keep as a reserve, or exclude each item; edit titles, categories and notes.
5. Prepare the selected source text for an optional LLM draft, then edit and format the Chinese brief.

The local NiceGUI workbench exposes collection, review, drafting and finalization. It starts jobs only when requested; the project installs no scheduler and publishes no brief automatically.

## My contribution

I defined the editorial workflow, designed the review states and source-tracing fields, wrote the English-to-Chinese drafting instructions, and developed the collection, review and formatting implementation with AI coding assistance. I also wrote tests for review navigation, saved decisions, source preparation and formatting.

The important editorial checks are names, figures, currencies, dates, attribution and project status. Automation proposes candidates and drafts; it does not establish that a story is true. The public examples illustrate the checks without making measured productivity or accuracy claims.

## Two examples of editorial judgment

| Source says | Avoid writing | Reference edit |
|---|---|---|
| A fictional authority proposes an 18 km road rehabilitation project, subject to funding approval; no contractor is named. | “18公里公路项目已获批并授标。” | “拟修复18公里公路，资金仍待批准，承包商尚未确定。” |
| A fictional operator commissions a 2 MW solar mini-grid; the tariff is not disclosed. | “2兆瓦光伏项目将全面降低电价。” | “2兆瓦光伏微电网投运；公告未披露电价。” |

These are fictional training examples, not claims about actual Nigerian projects. The [case notes](docs/editorial-examples.md) explain the duplicate, promotional and failed-fetch items too.

## Run the full local workbench

```sh
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python src/news_ui.py
```

The workbench binds to `127.0.0.1:8080`. The collection launcher is cross-platform; Windows wrappers are retained for convenience. See [usage](docs/usage.md) for configuration and manual commands.

Live RSS collection and article fetching require network access. Optional AI screening and drafting require your own provider configuration. Selected article text is sent to the configured provider when you request those steps. The offline demo requires neither.

## Repository guide

| Path | Purpose |
|---|---|
| `src/discover.py`, `src/rss_prescreen.py` | Discovery and initial screening |
| `src/processors/`, `src/filter.py`, `src/dedupe.py` | Scoring, freshness checks and grouping |
| `src/ui_backend.py`, `src/news_ui.py` | Review decisions and the local workbench |
| `src/prepare_llm_input.py`, `src/generate_digest.py` | Selected-source preparation and optional drafting |
| `prompts/` | Editing instructions and structured output requirements |
| `config/`, `config.yaml` | Illustrative topics, entities and scoring heuristics |
| `examples/editorial_cases.json` | Fictional source material and reference edits |
| `scripts/run_demo.py`, `docs/demo.html` | Reproducible offline walkthrough |
| `scripts/preview_workbench.py` | Original website with isolated fictional data |
| `tests/` | Automated checks |

```sh
python -m unittest discover -s tests -v
```

## Scope and limits

- This is a portfolio edition. It contains no operational news archive, employer database, client deliverable or private Git history. The entity list and topic configuration are examples, not an employer watchlist or a validated ranking model.
- The offline demo covers review and formatting. It does not validate live crawling, model quality or current news facts. Review heuristic scores are ranking aids, not confidence probabilities.
- Real sources may fail, repeat an error or change after collection. Check facts against original sources and respect their access and reuse terms. No article corpus is included here.
- No open-source license has been selected. Making this portfolio available for inspection does not grant a general license to reuse its code; third-party dependencies retain their own licenses.

