#!/usr/bin/env python3
"""Repair printed page numbers in a page corpus.

The repair is conservative: it fills confident monotonic gaps and flags uncertain
pages instead of inventing unsupported citations.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

BANGLA_DIGITS = "০১২৩৪৫৬৭৮৯"
ASCII_TO_BANGLA = str.maketrans("0123456789", BANGLA_DIGITS)

TRUSTED_PAGE_TYPES = {
    "normal_poem_page",
    "poem_or_text_page",
    "poem_start_or_short_page",
}


def bangla_number(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value).translate(ASCII_TO_BANGLA)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def layout_candidate_map(layout_path: Path | None) -> dict[tuple[str, int], list[dict[str, Any]]]:
    if layout_path is None:
        return {}
    rows = read_jsonl(layout_path)
    by_page: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        book_id = row.get("book_id")
        scan_page = row.get("scan_page")
        if not book_id or not isinstance(scan_page, int):
            continue
        for candidate in row.get("printed_page_candidates") or []:
            if not isinstance(candidate.get("value"), int):
                continue
            normalized = dict(candidate)
            normalized.setdefault("source", "tesseract_tsv_line")
            by_page[(book_id, scan_page)].append(normalized)
    return by_page


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def merge_layout_candidates(
    rows: list[dict[str, Any]],
    layout_candidates: dict[tuple[str, int], list[dict[str, Any]]],
) -> None:
    if not layout_candidates:
        return
    for row in rows:
        book_id = row.get("book_id")
        scan_page = row.get("scan_page")
        if not book_id or not isinstance(scan_page, int):
            continue
        additions = layout_candidates.get((book_id, scan_page), [])
        if not additions and row.get("physical_book_id"):
            additions = layout_candidates.get((row["physical_book_id"], scan_page), [])
        if not additions:
            continue

        row["layout_printed_page_candidates"] = additions
        existing = []
        seen = set()
        for candidate in row.get("printed_page_candidates") or []:
            normalized = dict(candidate)
            normalized.setdefault("source", "page_corpus_ocr_zone")
            key = (
                normalized.get("value"),
                normalized.get("zone"),
                normalized.get("source"),
                normalized.get("raw"),
            )
            existing.append(normalized)
            seen.add(key)
        for candidate in additions:
            key = (
                candidate.get("value"),
                candidate.get("zone"),
                candidate.get("source"),
                candidate.get("raw"),
            )
            if key not in seen:
                existing.append(candidate)
                seen.add(key)
        row["printed_page_candidates"] = existing


def best_visible_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    page_type = row.get("page_type")
    if page_type not in TRUSTED_PAGE_TYPES:
        return None
    candidates = row.get("printed_page_candidates") or []
    if not candidates:
        return None

    def rank(candidate: dict[str, Any]) -> tuple[int, int, float, int]:
        return (
            1 if candidate.get("source") == "tesseract_tsv_line" else 0,
            1 if candidate.get("script") == "bangla" else 0,
            1 if candidate.get("zone") == "bottom" else 0,
            float(candidate.get("avg_conf") or 0),
            len(str(candidate.get("raw") or "")),
        )

    ordered = sorted(candidates, key=rank, reverse=True)
    valid = [c for c in ordered if isinstance(c.get("value"), int) and 1 <= int(c.get("value")) <= 500]
    if not valid:
        return None
    return valid[0]


def visible_basis(candidate: dict[str, Any]) -> str:
    if candidate.get("source") == "tesseract_tsv_line":
        return "visible_layout_candidate"
    return "visible_ocr_candidate"


def scan_page(row: dict[str, Any], fallback_idx: int) -> int:
    value = row.get("scan_page")
    return int(value) if isinstance(value, int) else fallback_idx + 1


def supported_offsets(
    rows: list[dict[str, Any]],
    raw_candidates: list[tuple[int, dict[str, Any]]],
) -> set[int] | None:
    offsets: dict[int, list[int]] = defaultdict(list)
    for idx, candidate in raw_candidates:
        offsets[int(candidate["value"]) - scan_page(rows[idx], idx)].append(scan_page(rows[idx], idx))
    if not offsets:
        return None

    supported = {offset for offset, scans in offsets.items() if len(scans) >= 3}
    if not supported:
        return None
    return supported


def pairwise_rejected_indices(raw_candidates: list[tuple[int, dict[str, Any]]]) -> set[int]:
    rejected_indices: set[int] = set()
    for pos, (idx, candidate) in enumerate(raw_candidates):
        prev_anchor = raw_candidates[pos - 1] if pos > 0 else None
        next_anchor = raw_candidates[pos + 1] if pos + 1 < len(raw_candidates) else None
        value = int(candidate["value"])
        if prev_anchor and value <= int(prev_anchor[1]["value"]):
            rejected_indices.add(idx)
            continue
        if next_anchor and value >= int(next_anchor[1]["value"]):
            rejected_indices.add(idx)
    return rejected_indices


def fill_contiguous_offset_runs(rows: list[dict[str, Any]], offset: int) -> None:
    anchor_indices = [
        idx
        for idx, row in enumerate(rows)
        if row.get("printed_page_basis") in {"visible_ocr_candidate", "visible_layout_candidate"}
        and isinstance(row.get("printed_page_fixed"), int)
        and int(row["printed_page_fixed"]) - scan_page(row, idx) == offset
    ]
    seen: set[int] = set()
    for anchor_idx in anchor_indices:
        for direction in (-1, 1):
            idx = anchor_idx + direction
            while 0 <= idx < len(rows):
                row = rows[idx]
                if row.get("page_type") not in TRUSTED_PAGE_TYPES:
                    break
                inferred = scan_page(row, idx) + offset
                if inferred < 1:
                    break
                if row.get("printed_page_fixed") is None and idx not in seen:
                    row["printed_page_fixed"] = inferred
                    row["printed_page_label_bn"] = bangla_number(inferred)
                    row["printed_page_basis"] = "sequence_offset_inferred"
                    row["sequence_confidence"] = 0.7
                    seen.add(idx)
                idx += direction


def repair_book(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: int(row.get("scan_page") or 0))
    raw_candidates: list[tuple[int, dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        candidate = best_visible_candidate(row)
        if candidate is not None:
            raw_candidates.append((idx, candidate))

    offsets = supported_offsets(rows, raw_candidates)
    if offsets is None:
        rejected_indices = pairwise_rejected_indices(raw_candidates)
    else:
        rejected_indices = {
            idx
            for idx, candidate in raw_candidates
            if int(candidate["value"]) - scan_page(rows[idx], idx) not in offsets
        }

    for idx, row in enumerate(rows):
        flags = set(row.get("flags") or [])
        candidate = best_visible_candidate(row)
        if idx in rejected_indices:
            flags.add("offset_outlier_printed_page_candidate" if offsets is not None else "nonmonotonic_printed_page_candidate")
            row["printed_page_fixed"] = None
            row["printed_page_label_bn"] = None
            row["printed_page_basis"] = "suspect_visible_candidate"
            row["sequence_confidence"] = 0.2
        elif candidate is not None:
            value = int(candidate["value"])
            row["printed_page_fixed"] = value
            row["printed_page_label_bn"] = bangla_number(value)
            row["printed_page_basis"] = visible_basis(candidate)
            row["printed_page_candidate_source"] = candidate.get("source")
            row["sequence_confidence"] = 0.8
        else:
            row["printed_page_fixed"] = None
            row["printed_page_label_bn"] = None
            row["printed_page_basis"] = "missing"
            row["sequence_confidence"] = 0.0
            if row.get("page_type") in TRUSTED_PAGE_TYPES:
                flags.add("missing_printed_page_candidate")
        row["flags"] = sorted(flags)

    if offsets is not None:
        for row in rows:
            row["supported_printed_page_offsets"] = sorted(offsets)
        for offset in sorted(offsets):
            fill_contiguous_offset_runs(rows, offset)

    anchors = [
        (idx, int(row["printed_page_fixed"]))
        for idx, row in enumerate(rows)
        if row.get("printed_page_basis") in {"visible_ocr_candidate", "visible_layout_candidate"}
        and isinstance(row.get("printed_page_fixed"), int)
    ]

    if len(anchors) < 2:
        return

    for (left_idx, left_page), (right_idx, right_page) in zip(anchors, anchors[1:]):
        scan_delta = right_idx - left_idx
        page_delta = right_page - left_page
        if scan_delta <= 1:
            continue
        if page_delta != scan_delta:
            for gap_idx in range(left_idx + 1, right_idx):
                flags = set(rows[gap_idx].get("flags") or [])
                flags.add(f"sequence_gap_mismatch:{left_page}->{right_page}")
                rows[gap_idx]["flags"] = sorted(flags)
            continue

        for gap_idx in range(left_idx + 1, right_idx):
            row = rows[gap_idx]
            if row.get("page_type") not in TRUSTED_PAGE_TYPES:
                continue
            inferred = left_page + (gap_idx - left_idx)
            if row.get("printed_page_fixed") is None:
                row["printed_page_fixed"] = inferred
                row["printed_page_label_bn"] = bangla_number(inferred)
                row["printed_page_basis"] = "sequence_inferred"
                row["sequence_confidence"] = 0.65


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair printed page sequence in page corpus JSONL.")
    parser.add_argument("--input", default="metadata_reports/page-corpus.classified.jsonl")
    parser.add_argument("--output", default="metadata_reports/page-corpus.repaired.jsonl")
    parser.add_argument("--layout", help="Optional page-layout JSONL with TSV page-number candidates.")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.input))
    merge_layout_candidates(rows, layout_candidate_map(Path(args.layout) if args.layout else None))
    by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_book[row.get("book_id", "unknown")].append(row)
    for book_rows in by_book.values():
        repair_book(book_rows)

    rows.sort(key=lambda row: (row.get("book_id") or "", int(row.get("scan_page") or 0)))
    write_jsonl(Path(args.output), rows)
    print(f"Wrote {len(rows)} repaired page records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
