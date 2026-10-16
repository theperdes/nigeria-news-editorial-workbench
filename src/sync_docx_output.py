from __future__ import annotations

import argparse
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from pipeline_paths import add_run_date_arg, effective_run_date, write_text_with_latest


ROOT_DIR = Path(__file__).resolve().parents[1]
DOCX_SUFFIX = ".docx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync the human-edited daily digest from a Word document back to llm_output.md."
    )
    parser.add_argument(
        "--docx",
        default=None,
        help="Word document to read. Defaults to the newest .docx file in data/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the extracted digest instead of writing llm_output.md.",
    )
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_date = effective_run_date(args.run_date)
    docx_path = resolve_docx_path(args.docx)
    paragraphs = read_docx_paragraphs(docx_path)
    extracted = extract_run_digest(paragraphs, run_date)
    markdown = paragraphs_to_markdown(extracted)

    if args.dry_run:
        print(markdown)
        return

    output_path = write_text_with_latest(ROOT_DIR, run_date, "llm_output.md", markdown)
    print(f"Read edited digest from {docx_path}")
    print(f"Extracted {len(extracted)} paragraphs for {run_date}")
    print(f"Updated {output_path}")
    print(f"Synced latest copy to {ROOT_DIR / 'data' / 'llm_output.md'}")
    print("Left llm_output_raw.md unchanged")


def resolve_docx_path(path_value: str | None) -> Path:
    if path_value:
        path = Path(path_value)
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.exists():
            raise FileNotFoundError(f"Word document not found: {path}")
        if path.suffix.casefold() != DOCX_SUFFIX:
            raise ValueError(f"Expected a .docx file, got: {path}")
        return path

    candidates = sorted(
        (ROOT_DIR / "data").glob(f"*{DOCX_SUFFIX}"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No .docx files found in data/. Pass --docx PATH.")
    return candidates[0]


def read_docx_paragraphs(path: Path) -> list[str]:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)

    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = "".join(texts).replace("\xa0", " ").strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def extract_run_digest(paragraphs: list[str], run_date: str) -> list[str]:
    start_index = find_start_index(paragraphs, run_date)
    end_index = find_end_index(paragraphs, start_index)
    extracted = paragraphs[start_index:end_index]
    if not extracted:
        raise RuntimeError(f"No paragraphs extracted for {run_date}")
    return extracted


def find_start_index(paragraphs: list[str], run_date: str) -> int:
    date_value = datetime.strptime(run_date, "%Y-%m-%d")
    dotted_date = date_value.strftime("%Y.%m.%d")
    body_date = f"{date_value.year}\u5e74{date_value.month}\u6708{date_value.day}\u65e5"

    overview_pattern = re.compile(
        r"^\u4eca\u65e5\u65b0\u95fb\u6807\u9898\u901f\u89c8\s*"
        + re.escape(f"({dotted_date})")
    )
    body_pattern = re.compile(
        "^" + re.escape(body_date) + r"\u5c3c\u65e5\u5229\u4e9a\u65b0\u95fb"
    )

    for index, paragraph in enumerate(paragraphs):
        if overview_pattern.search(paragraph):
            return index

    for index, paragraph in enumerate(paragraphs):
        if body_pattern.search(paragraph):
            return index

    raise RuntimeError(
        f"Could not find {run_date} in Word document. Expected an overview heading "
        f"with ({dotted_date}) or a body heading beginning with {body_date}."
    )


def find_end_index(paragraphs: list[str], start_index: int) -> int:
    next_overview_pattern = re.compile(
        r"^\u4eca\u65e5\u65b0\u95fb\u6807\u9898\u901f\u89c8\s*\(\d{4}\.\d{2}\.\d{2}\)"
    )
    next_body_pattern = re.compile(
        r"^\d{4}\u5e74\d{1,2}\u6708\d{1,2}\u65e5\u5c3c\u65e5\u5229\u4e9a\u65b0\u95fb"
    )

    started_at_overview = bool(next_overview_pattern.search(paragraphs[start_index]))
    for index in range(start_index + 1, len(paragraphs)):
        paragraph = paragraphs[index]
        if next_overview_pattern.search(paragraph):
            return index
        if not started_at_overview and next_body_pattern.search(paragraph):
            return index
    return len(paragraphs)


def paragraphs_to_markdown(paragraphs: list[str]) -> str:
    return "\n\n".join(paragraphs).strip() + "\n"


if __name__ == "__main__":
    main()
