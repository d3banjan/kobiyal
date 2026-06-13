#!/usr/bin/env python3
"""Propose poem page spans from a repaired OCR page corpus.

Outputs sidecar candidates only. It never edits poem JSON.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for direct python without uv.
    tqdm = None

BANGLA_TO_ASCII = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

COLLECTION_TO_BOOK_ID = {
    "ধূসর পাণ্ডুলিপি": "dhusar-pandulipi",
    "বনলতা সেন": "banalata-sen",
    "সাতটি তারার তিমির": "satti-tarar-timir",
    "বেলা অবেলা কালবেলা": "bela-abela-kalabela",
    "মহাপৃথিবী": "mahaprithibi",
    "রূপসী বাংলা": "rupasi-bangla",
}

OCR_EQUIVALENCES = [
    ["ি", "ী"],
    ["ু", "ূ"],
    ["ন", "ণ"],
    ["য", "য়", "য়"],
    ["র", "ব"],
    ["দ", "ধ"],
    ["ৎ", "ত"],
    ["।", "|", "১"],
]

TRUSTED_PAGE_TYPES = {
    "normal_poem_page",
    "poem_or_text_page",
    "poem_start_or_short_page",
}


def normalize(text: str) -> str:
    text = (text or "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    text = text.translate(BANGLA_TO_ASCII)
    text = re.sub(r"[^\u0980-\u09FF0-9\s]+", " ", text)
    for group in OCR_EQUIVALENCES:
        canonical = group[0]
        for variant in group[1:]:
            text = text.replace(variant, canonical)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> set[str]:
    return {word for word in normalize(text).split() if len(word) >= 3}


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


def progress_iter(iterable, **kwargs):
    if tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


def load_poems(poems_dir: Path, limit: int | None) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for path in sorted(glob.glob(str(poems_dir / "*.json"))):
        with open(path, encoding="utf-8") as f:
            rows.append((Path(path).name, json.load(f)))
        if limit is not None and len(rows) >= limit:
            break
    return rows


def page_text(row: dict[str, Any]) -> str:
    if "_match_text" in row:
        return row["_match_text"]
    return "\n".join(
        part
        for part in [
            row.get("normalized_match_text") or "",
            normalize(row.get("raw_ocr") or ""),
            normalize(row.get("raw_pdftotext") or ""),
        ]
        if part
    )


def prepare_page(row: dict[str, Any]) -> dict[str, Any]:
    match_text = page_text(row)
    row["_match_text"] = match_text
    row["_tokens"] = tokens(match_text)
    return row


def book_token_document_frequency(pages_by_book: dict[str, list[dict[str, Any]]]) -> dict[str, Counter[str]]:
    by_book: dict[str, Counter[str]] = {}
    for book_id, pages in pages_by_book.items():
        counter: Counter[str] = Counter()
        for page in pages:
            counter.update(page.get("_tokens") or tokens(page_text(page)))
        by_book[book_id] = counter
    return by_book


def candidate_books(poem: dict[str, Any], all_books: bool) -> set[str]:
    if all_books:
        return set(COLLECTION_TO_BOOK_ID.values())
    edition = poem.get("source_edition")
    if edition in COLLECTION_TO_BOOK_ID:
        return {COLLECTION_TO_BOOK_ID[edition]}
    return set(COLLECTION_TO_BOOK_ID.values())


def score_page(
    title: str,
    lines: list[str],
    body_tokens: set[str],
    page: dict[str, Any],
) -> tuple[float, list[str]]:
    text = page_text(page)
    text_tokens = page.get("_tokens") or tokens(text)
    evidence: list[str] = []
    score = 0.0

    if title and title in text:
        score += 12.0
        evidence.append("title_match")

    if lines:
        if lines[0] and lines[0] in text:
            score += 8.0
            evidence.append("first_line_match")
        if lines[-1] and lines[-1] in text:
            score += 8.0
            evidence.append("last_line_match")

    if body_tokens:
        overlap = len(body_tokens & text_tokens)
        pct = overlap / len(body_tokens)
        score += min(overlap, 40) * 0.4
        if overlap >= 8:
            evidence.append("body_token_overlap")
        if pct >= 0.35:
            evidence.append("high_body_coverage")

    if page.get("printed_page_fixed") is not None:
        score += 1.0
        evidence.append("page_sequence_present")

    return score, sorted(set(evidence))


def line_anchor_matches(
    line_norms: list[str],
    page: dict[str, Any],
    token_df: Counter[str],
    book_page_count: int,
) -> list[dict[str, Any]]:
    text = page_text(page)
    page_tokens = page.get("_tokens") or tokens(text)
    matches: list[dict[str, Any]] = []
    distinctive_limit = max(3, int(book_page_count * 0.08))

    for line_index, line in enumerate(line_norms):
        if not line:
            continue
        if line in text:
            matches.append({"line_index": line_index, "kind": "exact"})
            continue

        line_tokens = tokens(line)
        if len(line_tokens) < 4:
            continue
        overlap = line_tokens & page_tokens
        coverage = len(overlap) / len(line_tokens)
        distinctive = {token for token in line_tokens if token_df[token] <= distinctive_limit}
        distinctive_overlap = distinctive & page_tokens
        if coverage >= 0.82 and (
            not distinctive or len(distinctive_overlap) >= min(2, len(distinctive))
        ):
            matches.append({"line_index": line_index, "kind": "fuzzy"})

    return matches


def anchor_summary(
    title: str,
    line_norms: list[str],
    page: dict[str, Any],
    token_df: Counter[str],
    book_page_count: int,
) -> dict[str, Any] | None:
    if page.get("page_type") not in TRUSTED_PAGE_TYPES:
        return None

    text = page_text(page)
    title_match = bool(title and title in text)
    matches = line_anchor_matches(line_norms, page, token_df, book_page_count)
    exact_count = sum(1 for match in matches if match["kind"] == "exact")
    fuzzy_count = len(matches) - exact_count
    terminal_indexes = {0, len(line_norms) - 1} if line_norms else set()
    terminal_exact = any(
        match["kind"] == "exact" and match["line_index"] in terminal_indexes for match in matches
    )

    is_anchor = (
        (title_match and bool(matches))
        or exact_count >= 2
        or terminal_exact
        or (exact_count >= 1 and fuzzy_count >= 2)
        or fuzzy_count >= 4
    )
    if not is_anchor:
        return None

    return {
        "scan_page": int(page["scan_page"]),
        "printed_page": page.get("printed_page_fixed"),
        "title_match": title_match,
        "line_match_count": len(matches),
        "exact_line_match_count": exact_count,
        "fuzzy_line_match_count": fuzzy_count,
        "line_indexes": [match["line_index"] for match in matches],
        "exact_line_indexes": [
            match["line_index"] for match in matches if match["kind"] == "exact"
        ],
    }


def anchored_span(
    best: dict[str, Any],
    pages: list[dict[str, Any]],
    title: str,
    line_norms: list[str],
    token_df: Counter[str],
) -> tuple[int, int, dict[str, Any]]:
    best_scan = int(best["scan_page"])
    summary_by_scan: dict[int, dict[str, Any]] = {}
    weak_matches_by_scan: dict[int, list[dict[str, Any]]] = {}
    for page in pages:
        scan = int(page["scan_page"])
        summary = anchor_summary(title, line_norms, page, token_df, len(pages))
        if summary is not None:
            summary_by_scan[scan] = summary
        elif page.get("page_type") in TRUSTED_PAGE_TYPES:
            weak_matches_by_scan[scan] = line_anchor_matches(line_norms, page, token_df, len(pages))

    anchors = sorted(summary_by_scan.values(), key=lambda anchor: anchor["scan_page"])

    if not anchors:
        return (
            best_scan,
            best_scan,
            {
                "basis": "best_page_no_line_anchor",
                "best_scan_page": best_scan,
                "anchor_count": 0,
                "anchor_scan_pages": [],
                "line_match_count": 0,
                "exact_line_match_count": 0,
            },
        )

    anchors.sort(key=lambda anchor: anchor["scan_page"])
    seed_index = min(
        range(len(anchors)),
        key=lambda idx: (abs(anchors[idx]["scan_page"] - best_scan), anchors[idx]["scan_page"]),
    )
    left = right = seed_index
    while left > 0 and anchors[left]["scan_page"] - anchors[left - 1]["scan_page"] <= 2:
        left -= 1
    while right + 1 < len(anchors) and anchors[right + 1]["scan_page"] - anchors[right]["scan_page"] <= 2:
        right += 1

    cluster = anchors[left : right + 1]
    cluster_scans = {anchor["scan_page"] for anchor in cluster}
    line_indexes = [
        line_index
        for anchor in cluster
        for line_index in anchor.get("line_indexes", [])
    ]
    min_line_index = min(line_indexes) if line_indexes else None
    max_line_index = max(line_indexes) if line_indexes else None
    start_scan = min(anchor["scan_page"] for anchor in cluster)
    end_scan = max(anchor["scan_page"] for anchor in cluster)
    continuation_scans: list[int] = []

    def continuation_indexes(scan: int, direction: int) -> list[int]:
        matches = weak_matches_by_scan.get(scan, [])
        if not matches:
            return []
        indexes = [match["line_index"] for match in matches]
        exact_indexes = [match["line_index"] for match in matches if match["kind"] == "exact"]
        if direction > 0:
            if max_line_index is None:
                return []
            continuing = [index for index in indexes if index >= max_line_index - 1]
        else:
            if min_line_index is None:
                return []
            continuing = [index for index in indexes if index <= min_line_index + 1]

        exact_continuing = [index for index in exact_indexes if index in continuing]
        if len(continuing) >= 2 or exact_continuing:
            return continuing
        return []

    for direction in (-1, 1):
        while True:
            next_scan = start_scan - 1 if direction < 0 else end_scan + 1
            if next_scan in cluster_scans:
                break
            page = next((row for row in pages if int(row["scan_page"]) == next_scan), None)
            if page is None or page.get("page_type") not in TRUSTED_PAGE_TYPES:
                break
            indexes = continuation_indexes(next_scan, direction)
            if not indexes:
                break
            continuation_scans.append(next_scan)
            if direction < 0:
                start_scan = next_scan
                min_line_index = min(indexes)
            else:
                end_scan = next_scan
                max_line_index = max(indexes)

    return (
        start_scan,
        end_scan,
        {
            "basis": "line_anchor_cluster",
            "best_scan_page": best_scan,
            "anchor_count": len(cluster),
            "anchor_scan_pages": [anchor["scan_page"] for anchor in cluster],
            "continuation_scan_pages": sorted(continuation_scans),
            "line_match_count": sum(anchor["line_match_count"] for anchor in cluster),
            "exact_line_match_count": sum(anchor["exact_line_match_count"] for anchor in cluster),
        },
    )


def printed_range(pages_by_scan: dict[int, dict[str, Any]], start: int, end: int) -> tuple[int | None, int | None]:
    start_row = pages_by_scan.get(start, {})
    end_row = pages_by_scan.get(end, {})
    return start_row.get("printed_page_fixed"), end_row.get("printed_page_fixed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose poem page spans from OCR page corpus.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.repaired.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--output", default="metadata_reports/poem-span-candidates.jsonl")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--all-books", action="store_true", help="Search all primary books for every poem.")
    parser.add_argument("--min-score", type=float, default=10.0)
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bar.")
    args = parser.parse_args()

    pages = read_jsonl(Path(args.page_corpus))
    pages_by_book: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        page = prepare_page(page)
        pages_by_book.setdefault(page.get("book_id", "unknown"), []).append(page)
    for book_pages in pages_by_book.values():
        book_pages.sort(key=lambda row: int(row.get("scan_page") or 0))
    token_df_by_book = book_token_document_frequency(pages_by_book)

    poems = load_poems(Path(args.poems_dir), args.limit)
    poem_iter = poems
    if not args.no_progress:
        poem_iter = progress_iter(
            poems,
            desc="propose spans",
            unit="poem",
            dynamic_ncols=True,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} poems [{elapsed}<{remaining}, {rate_fmt}]",
        )

    output_rows: list[dict[str, Any]] = []
    for filename, poem in poem_iter:
        poem_token_set = tokens(poem.get("body_bn") or "")
        title_norm = normalize(poem.get("title_bn") or "")
        line_norms = [
            normalized
            for line in (poem.get("body_bn") or "").splitlines()
            if len((normalized := normalize(line))) >= 8
        ]
        candidates = []
        for book_id in candidate_books(poem, args.all_books):
            for page in pages_by_book.get(book_id, []):
                score, evidence = score_page(title_norm, line_norms, poem_token_set, page)
                if score >= args.min_score:
                    candidates.append((score, evidence, page))

        candidates.sort(key=lambda item: item[0], reverse=True)
        if not candidates:
            output_rows.append(
                {
                    "filename": filename,
                    "poem_id": poem.get("id"),
                    "title_bn": poem.get("title_bn"),
                    "status": "no_candidate",
                    "candidates": [],
                }
            )
            continue

        best_score, best_evidence, best_page = candidates[0]
        book_pages = pages_by_book.get(best_page["book_id"], [])
        pages_by_scan = {int(row["scan_page"]): row for row in book_pages}
        span_start, span_end, span_info = anchored_span(
            best_page,
            book_pages,
            title_norm,
            line_norms,
            token_df_by_book.get(best_page["book_id"], Counter()),
        )
        printed_start, printed_end = printed_range(pages_by_scan, span_start, span_end)
        runner_up_gap = best_score - candidates[1][0] if len(candidates) > 1 else None
        strong_line_or_body = bool(
            {"first_line_match", "last_line_match", "high_body_coverage"} & set(best_evidence)
        )
        has_span_anchor = (
            span_info.get("anchor_count", 0) > 0
            and (
                span_info.get("exact_line_match_count", 0) > 0
                or span_info.get("line_match_count", 0) >= 2
            )
        )
        status = "needs_manual_review"
        if (
            has_span_anchor
            and
            best_score >= 18
            and "title_match" in best_evidence
            and strong_line_or_body
            and (runner_up_gap is None or runner_up_gap >= 4)
        ):
            status = "accepted_candidate"
        elif (
            has_span_anchor
            and best_score >= 24
            and strong_line_or_body
            and (runner_up_gap is None or runner_up_gap >= 6)
        ):
            status = "accepted_candidate"
        elif (
            has_span_anchor
            and best_score >= 24
            and "high_body_coverage" in best_evidence
            and span_info.get("line_match_count", 0) >= 12
            and span_info.get("exact_line_match_count", 0) >= 6
            and (runner_up_gap is None or runner_up_gap >= 4)
        ):
            status = "accepted_candidate"

        if runner_up_gap is not None and runner_up_gap < 4:
            status = "ambiguous"

        output_rows.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_edition": poem.get("source_edition"),
                "candidate_book_id": best_page.get("book_id"),
                "candidate_pdf_file": best_page.get("pdf_file"),
                "pdf_page_start": span_start,
                "pdf_page_end": span_end,
                "printed_page_start": printed_start,
                "printed_page_end": printed_end,
                "score": round(best_score, 3),
                "evidence": best_evidence,
                "span_basis": span_info["basis"],
                "best_pdf_page": span_info["best_scan_page"],
                "span_anchor_count": span_info["anchor_count"],
                "span_anchor_scan_pages": span_info["anchor_scan_pages"],
                "span_continuation_scan_pages": span_info.get("continuation_scan_pages", []),
                "span_line_match_count": span_info["line_match_count"],
                "span_exact_line_match_count": span_info["exact_line_match_count"],
                "status": status,
                "runner_up_count": max(0, len(candidates) - 1),
                "runner_up_gap": round(runner_up_gap, 3) if runner_up_gap is not None else None,
            }
        )

    write_jsonl(Path(args.output), output_rows)
    print(f"Wrote {len(output_rows)} poem span candidates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
