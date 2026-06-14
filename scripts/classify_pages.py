#!/usr/bin/env python3
"""Reclassify page corpus rows from structural features.

This script reads/writes JSONL sidecars only.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from logical_sections import apply_logical_section


def normalize(text: str) -> str:
    text = re.sub(r"[\s\u200b\u200c\u200d]+", "", text or "")
    return text


def looks_like_contents_page(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 5:
        return False

    entry_like = 0
    trailing_page_refs = 0
    digit_tokens = 0
    for line in lines:
        tokens = re.findall(r"[০-৯0-9]{1,3}", line)
        if not tokens or not re.search(r"[\u0980-\u09FF]", line):
            continue
        digit_tokens += len(tokens)
        compact = re.sub(r"\s+", " ", line)
        if len(compact) <= 54:
            entry_like += 1
        if re.search(r"[\u0980-\u09FF][^\n]{0,48}[০-৯0-9]{1,3}\s*$", compact):
            trailing_page_refs += 1

    if digit_tokens < 5 or entry_like < 4:
        return False
    return trailing_page_refs >= 3 or entry_like >= max(5, len(lines) // 3)


def classify(record: dict[str, Any]) -> str:
    features = record.get("layout_features") or {}
    text = normalize(
        "\n".join(
            [
                record.get("raw_ocr") or "",
                record.get("raw_pdftotext") or "",
                record.get("normalized_match_text") or "",
            ]
        )
    )
    line_count = int(features.get("line_count") or 0)
    char_count = int(features.get("char_count") or 0)
    long_lines = int(features.get("long_line_count") or 0)
    centeredish = int(features.get("centeredish_line_count") or 0)
    candidates = record.get("printed_page_candidates") or []

    if char_count < 25 or line_count <= 1:
        return "blank_or_near_blank"
    if re.search(r"প্রকাশক|মুদ্রক|প্রথমমুদ্রণ|প্রথমসংস্করণ|প্রচ্ছদ", text):
        return "publisher_page"
    if re.search(r"^[\"'‘’“”।]*(সূচ|সুচ)", text):
        return "front_matter"
    if looks_like_contents_page(
        "\n".join(
            [
                record.get("raw_ocr") or "",
                record.get("raw_pdftotext") or "",
            ]
        )
    ):
        return "front_matter"
    if re.search(r"উৎসর্গ|ভূমিকা|রচনাকাল", text) and long_lines < 8:
        return "front_matter"
    if re.search(r"জীবনানন্দেরচেতনাজগৎ|প্রবন্ধ|আলোচনা|পর্যালোচনা", text):
        return "critical_prose_page"
    if line_count <= 8 and centeredish >= max(2, line_count // 2):
        return "section_title_page"
    if long_lines <= 2 and line_count <= 14:
        return "poem_start_or_short_page"
    if candidates and line_count >= 8:
        return "normal_poem_page"
    return "poem_or_text_page"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify page corpus rows by structure.")
    parser.add_argument("--input", default="metadata_reports/page-corpus.jsonl")
    parser.add_argument("--output", default="metadata_reports/page-corpus.classified.jsonl")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.input))
    for row in rows:
        previous = row.get("page_type")
        current = classify(row)
        row["page_type"] = current
        apply_logical_section(row)
        flags = set(row.get("flags") or [])
        if previous and previous != current:
            flags.add(f"page_type_changed:{previous}->{current}")
        row["flags"] = sorted(flags)

    write_jsonl(Path(args.output), rows)
    print(f"Wrote {len(rows)} classified page records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
