from __future__ import annotations

import re


DROP_LINE_PATTERNS = [
    r"\badvertisement\b",
    r"\badvertorial\b",
    r"\brelated news\b",
    r"\bread also\b",
    r"\bsponsored\b",
    r"\bshare this\b",
    r"\bsubscribe\b",
    r"\bfollow us\b",
    r"\brecommended\b",
    r"\bmore stories\b",
    r"\ball rights reserved\b",
    r"\bcopyright\b",
    r"\bjoin our\b",
    r"\bsign up\b",
    r"\bnewsletter\b",
]

MOJIBAKE_REPLACEMENTS = {
    "鈥檚": "'s",
    "鈥檛": "n't",
    "鈥檙": "'r",
    "鈥檒": "'l",
    "鈥檝": "'v",
    "鈥": "'",
    "鈧?": "₦",
    "閳": "",
}


def clean_article_text(text: str) -> str:
    cleaned_text = fix_mojibake(text)
    lines = cleaned_text.splitlines()
    kept_lines: list[str] = []
    seen_lines: set[str] = set()

    for line in lines:
        normalized_line = normalize_line(line)
        if should_drop_line(normalized_line):
            continue

        dedupe_key = normalized_line.casefold()
        if dedupe_key in seen_lines:
            continue

        seen_lines.add(dedupe_key)
        kept_lines.append(normalized_line)

    return "\n".join(kept_lines).strip()


def fix_mojibake(text: str) -> str:
    fixed_text = text
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        fixed_text = fixed_text.replace(bad, good)
    return fixed_text


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def should_drop_line(line: str) -> bool:
    if not line:
        return True

    if len(line) < 20 and not re.search(r"\d", line):
        return True

    lowered_line = line.casefold()
    return any(re.search(pattern, lowered_line) for pattern in DROP_LINE_PATTERNS)
