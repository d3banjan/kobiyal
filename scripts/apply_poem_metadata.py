#!/usr/bin/env python3
"""Apply gated poem metadata from span candidates to poem JSON files.

This script is intentionally conservative. It only applies page citations when
the span candidate is accepted, has printed book page numbers, and does not
contradict an existing known collection assignment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BOOK_META = {
    "dhusar-pandulipi": {
        "title_bn": "ধূসর পাণ্ডুলিপি",
        "publication_year": 1936,
        "phase_id": "dhusar-pandulipi",
    },
    "jhara-palak": {
        "title_bn": "ঝরা পালক",
        "publication_year": 1927,
        "phase_id": "jhara-palak",
    },
    "banalata-sen": {
        "title_bn": "বনলতা সেন",
        "publication_year": 1942,
        "phase_id": "banalata-sen",
    },
    "mahaprithibi": {
        "title_bn": "মহাপৃথিবী",
        "publication_year": 1944,
        "phase_id": "mahaprithibi-timir",
    },
    "satti-tarar-timir": {
        "title_bn": "সাতটি তারার তিমির",
        "publication_year": 1948,
        "phase_id": "mahaprithibi-timir",
    },
    "rupasi-bangla": {
        "title_bn": "রূপসী বাংলা",
        "publication_year": 1957,
        "phase_id": "rupasi-bangla",
    },
    "bela-abela-kalabela": {
        "title_bn": "বেলা অবেলা কালবেলা",
        "publication_year": 1961,
        "phase_id": "posthumous-manuscript",
    },
}

UNKNOWN_COLLECTION = "সংকলন অজানা"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def book_source(row: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "primary",
        "title_bn": meta["title_bn"],
        "publisher_bn": None,
        "edition_bn": None,
        "publication_year": meta["publication_year"],
        "isbn": None,
        "purchase_url": None,
        "page_start": row["printed_page_start"],
        "page_end": row["printed_page_end"],
        "page_label_bn": None,
        "page_basis": "printed_page",
        "note_bn": "OCR ও পৃষ্ঠা-ক্রম মিলিয়ে প্রস্তাবিত মুদ্রিত পৃষ্ঠা; পাঠ-প্রুফরিডিং আলাদা ধাপ।",
    }


def has_span_anchor_evidence(row: dict[str, Any]) -> bool:
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) <= 0:
        return False
    if int(row.get("span_exact_line_match_count") or 0) <= 0 and int(row.get("span_line_match_count") or 0) < 2:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    page_span = end - start + 1
    if page_span >= 5 and int(row.get("span_anchor_count") or 0) < page_span:
        return False
    return True


def is_eligible(row: dict[str, Any], poem: dict[str, Any], allow_legacy_candidates: bool) -> tuple[bool, str]:
    if poem.get("poet_id") != "jibanananda-das":
        return False, "not_jibanananda"
    if row.get("status") != "accepted_candidate":
        return False, "not_accepted"
    if not isinstance(row.get("printed_page_start"), int) or not isinstance(row.get("printed_page_end"), int):
        return False, "missing_printed_page"
    if not allow_legacy_candidates and not has_span_anchor_evidence(row):
        return False, "missing_span_anchor_evidence"

    meta = BOOK_META.get(row.get("candidate_book_id"))
    if not meta:
        return False, "unknown_candidate_book"

    current_edition = poem.get("source_edition")
    if current_edition != UNKNOWN_COLLECTION and current_edition != meta["title_bn"]:
        return False, "known_collection_conflict"

    return True, "eligible"


def apply_metadata(row: dict[str, Any], poem: dict[str, Any]) -> bool:
    meta = BOOK_META[row["candidate_book_id"]]
    before = json.dumps(poem, ensure_ascii=False, sort_keys=True)

    if poem.get("source_edition") == UNKNOWN_COLLECTION:
        poem["source_edition"] = meta["title_bn"]
        poem["source_year"] = meta["publication_year"]
        poem["phase_id"] = meta["phase_id"]

    source = book_source(row, meta)
    existing_sources = poem.get("book_sources") or []
    next_sources = [
        item
        for item in existing_sources
        if item.get("title_bn") != source["title_bn"] or item.get("role") != source["role"]
    ]
    next_sources.append(source)
    poem["book_sources"] = next_sources

    return before != json.dumps(poem, ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply gated poem metadata from span candidates.")
    parser.add_argument("--candidates", default="metadata_reports/poem-span-candidates.full.layout.normal.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-legacy-candidates",
        action="store_true",
        help="Allow accepted candidates that do not include deterministic span-anchor evidence.",
    )
    args = parser.parse_args()

    poems_dir = Path(args.poems_dir)
    rows = read_jsonl(Path(args.candidates))
    summary: dict[str, int] = {}
    changed: list[str] = []

    for row in rows:
        filename = row.get("filename")
        if not filename:
            continue
        path = poems_dir / filename
        if not path.exists():
            summary["missing_poem_file"] = summary.get("missing_poem_file", 0) + 1
            continue

        poem = read_json(path)
        eligible, reason = is_eligible(row, poem, args.allow_legacy_candidates)
        summary[reason] = summary.get(reason, 0) + 1
        if not eligible:
            continue

        if apply_metadata(row, poem):
            changed.append(filename)
            if not args.dry_run:
                write_json(path, poem)

    print(json.dumps({"summary": summary, "changed": changed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
