# Using the workbench

## Original website with fictional demo records

Install `requirements.txt` into a virtual environment, then run:

```sh
python scripts/preview_workbench.py
```

Open `http://127.0.0.1:8791/`. This uses the original `src/news_ui.py`, not the separate HTML walkthrough. It preserves the dashboard, news review, digest editing and history areas, plus the original alternate layouts at `/classic`, `/preview-c` and `/preview-c2`.

The preview seeds only fictional review records and an authored reference brief in a temporary directory. It does not seed fabricated collection counters or call the collection/AI services. Progress badges describe the seeded record state, not real model or crawling runs. Review and editing operations affect only that temporary demo session; the directory is removed when the preview process exits normally.

Demo mode blocks all background jobs and disables the generation button. To run the full workflow with your own separately configured data and services, use `python src/news_ui.py` as described below instead.

## Supplementary offline walkthrough

Run `python scripts/run_demo.py` from the repository root. Only the Python standard library is used. Open `docs/demo.html` locally or read `output/demo/brief.zh-CN.md`. This is a separate explanatory case page, not a replacement for the original website.

The five fictional cases include a proposed road project, a repeat of that item, a commissioned mini-grid, a promotional item and a failed fetch. The script applies explicitly authored reference decisions. It does not claim to choose those decisions autonomously or generate language with a live model.

Outputs include:

- `review-before.md` and `review.md`: editable review records before and after the scripted decisions;
- `llm_input.json`: selected source text prepared by the original source-matching functions;
- `brief.zh-CN.md`: formatted reference brief, using authored Chinese examples;
- `manifest.json`: case IDs, decisions and local artifact counts.

The demo writes only its dedicated output directory and `docs/demo.html`. Generated records are excluded from version control; the reviewed HTML walkthrough, screenshot and fictional fixture are included as portfolio assets. No telemetry, analytics or remote assets are used by the walkthrough.

## Full pipeline: explicit network and provider configuration

Create a virtual environment and install `requirements.txt` as described in the README. Copy `.env.example` to `.env` if you want the optional AI steps, then supply your own key and model name. Never commit `.env`.

```sh
# macOS / Linux
cp .env.example .env
# Windows PowerShell
# Copy-Item .env.example .env

python src/news_ui.py
```

Open `http://127.0.0.1:8080`. The workbench is designed for local use. It has no multi-user authentication and should not be exposed as a public service. A new random storage secret is used per process unless you explicitly configure `NEWS_UI_STORAGE_SECRET` locally.

For the full manual collection chain:

```sh
python scripts/collect.py --run-date 2026-01-15
```

Use the actual date you intend to collect; the command above is only a syntax example. Collection fetches remote RSS and article pages and includes optional configured AI stages. Without the necessary provider configuration those stages will fail. The collector stops at the first failing step and returns its nonzero status. It creates no scheduled task.

After collection, review `data/runs/YYYY-MM-DD/review.md` or use the local workbench. Check each source before selecting `include`, `maybe` or `exclude`. Fetch-failed items are for manual checking, not drafting from a headline alone.

Prepare and optionally draft the selected material:

```sh
python src/prepare_llm_input.py --run-date 2026-01-15
python src/generate_digest.py --run-date 2026-01-15
python src/pipeline_metrics.py --run-date 2026-01-15
```

The drafting step sends selected text to your configured provider. Review names, figures, currencies, dates, project stages, translations and unsupported background before using a brief. “整理并定稿” normalizes formatting; it is not a fact-checking decision.

## Configuration

- `config.yaml`: example search topics, rotation and collection limits;
- `config/entities.yaml`: a short example list written for this edition, without an employer dataset;
- `config/scoring_rules.yaml`: illustrative heuristics rather than calibrated confidence scores;
- `config/source_priority.yaml`: source ordering heuristics, not guarantees of reliability;
- `prompts/`: Chinese drafting constraints and structured output fields.

The optional provider settings deliberately contain no API key or fixed model recommendation. Supply a model available in your own account. Live service compatibility is separate from the offline demonstration and automated local tests.

## Tests

```sh
python -m unittest discover -s tests -v
```

The offline demo can be run without installing the full application. Full test discovery also imports the optional drafting and UI modules, so it requires the relevant dependencies from `requirements.txt`.

