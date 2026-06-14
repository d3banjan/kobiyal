#!/usr/bin/env python3
"""Audit existing printed-page citations against OCR corpus evidence.

This is a review-only sidecar generator. It checks existing poem JSON
`book_sources` citations without mutating metadata.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import apply_poem_metadata as apply_meta
import propose_poem_spans as spans


UNKNOWN_COLLECTION = "সংকলন অজানা"


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def duplicate_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(re.findall(r'"(jibanananda-[^"]+)"', path.read_text(encoding="utf-8")))


def public_jibanananda_poems(poems_dir: Path, duplicates: set[str]) -> list[tuple[str, dict[str, Any]]]:
    poems = []
    for path in sorted(glob.glob(str(poems_dir / "*.json"))):
        poem = read_json(Path(path))
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if poem.get("id") in duplicates:
            continue
        poems.append((Path(path).name, poem))
    return poems


def primary_printed_sources(poem: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        source
        for source in poem.get("book_sources") or []
        if source.get("role") == "primary"
        and source.get("page_basis") == "printed_page"
        and isinstance(source.get("page_start"), int)
        and isinstance(source.get("page_end"), int)
    ]


def canonical_book_id(book_id: str | None) -> str | None:
    if not book_id:
        return None
    return apply_meta.BOOK_ALIASES.get(book_id, book_id)


def source_book_id(source: dict[str, Any]) -> str | None:
    title = source.get("title_bn")
    for book_id, meta in apply_meta.BASE_BOOK_META.items():
        if meta.get("title_bn") == title:
            return book_id
    return None


def page_number_range(start: int, end: int) -> list[int]:
    if end < start:
        return []
    return list(range(start, end + 1))


def page_text(row: dict[str, Any]) -> str:
    return spans.page_text(spans.prepare_page(dict(row)))


def compact(text: str) -> str:
    return re.sub(r"\s+", "", spans.normalize(text))


def normalized_poem_lines(body: str, min_chars: int) -> list[dict[str, Any]]:
    rows = []
    for line_index, raw in enumerate((body or "").splitlines()):
        normalized = spans.normalize(raw)
        if len(compact(normalized)) < min_chars:
            continue
        rows.append({"line_index": line_index, "raw": raw.strip(), "normalized": normalized})
    return rows


def page_index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        printed_page = row.get("printed_page_fixed")
        if not isinstance(printed_page, int):
            continue
        row_book_id = row.get("book_id")
        canonical = canonical_book_id(str(row_book_id or ""))
        if canonical:
            index[(canonical, printed_page)].append(row)
    return index


def exact_book_page_index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        printed_page = row.get("printed_page_fixed")
        row_book_id = row.get("book_id")
        if isinstance(printed_page, int) and isinstance(row_book_id, str):
            index[(row_book_id, printed_page)].append(row)
    return index


def book_page_ranges(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    ranges: dict[str, dict[str, int]] = {}
    for row in rows:
        printed_page = row.get("printed_page_fixed")
        if not isinstance(printed_page, int):
            continue
        book_id = canonical_book_id(str(row.get("book_id") or ""))
        if not book_id:
            continue
        existing = ranges.get(book_id)
        if existing is None:
            ranges[book_id] = {"min": printed_page, "max": printed_page}
            continue
        existing["min"] = min(existing["min"], printed_page)
        existing["max"] = max(existing["max"], printed_page)
    return ranges


def load_candidates(path: Path) -> dict[str, dict[str, Any]]:
    candidates = {}
    if not path.exists():
        return candidates
    for row in read_jsonl(path):
        filename = row.get("filename")
        if not filename:
            continue
        candidates[str(filename)] = row
    return candidates


def citation_evidence(
    poem: dict[str, Any],
    source: dict[str, Any],
    canonical_source_book: str,
    canonical_pages: dict[tuple[str, int], list[dict[str, Any]]],
    exact_pages: dict[tuple[str, int], list[dict[str, Any]]],
    page_ranges: dict[str, dict[str, int]],
    min_line_chars: int,
) -> dict[str, Any]:
    start = int(source["page_start"])
    end = int(source["page_end"])
    page_numbers = page_number_range(start, end)
    exact_rows = [row for page in page_numbers for row in exact_pages.get((canonical_source_book, page), [])]
    canonical_rows = [row for page in page_numbers for row in canonical_pages.get((canonical_source_book, page), [])]
    joined_text = "\n".join(page_text(row) for row in canonical_rows)
    normalized_text = spans.normalize(joined_text)
    title = str(poem.get("title_bn") or "")
    normalized_title = spans.normalize(title)
    title_match = bool(normalized_title and normalized_title in normalized_text)

    lines = normalized_poem_lines(str(poem.get("body_bn") or ""), min_chars=min_line_chars)
    line_matches = [
        line
        for line in lines
        if line["normalized"] and line["normalized"] in normalized_text
    ]
    first_line_match = bool(lines and lines[0]["normalized"] in normalized_text)
    exact_line_indexes = [int(line["line_index"]) for line in line_matches]

    page_token_set = spans.tokens(joined_text)
    body_token_set = spans.tokens(str(poem.get("body_bn") or ""))
    token_overlap = len(body_token_set & page_token_set)
    body_coverage = token_overlap / max(1, len(body_token_set))
    corpus_range = page_ranges.get(canonical_source_book)
    outside_corpus_range = False
    if corpus_range and not any(
        corpus_range["min"] <= page_number <= corpus_range["max"]
        for page_number in page_numbers
    ):
        outside_corpus_range = True

    return {
        "page_start": start,
        "page_end": end,
        "book_corpus_page_min": corpus_range["min"] if corpus_range else None,
        "book_corpus_page_max": corpus_range["max"] if corpus_range else None,
        "outside_corpus_range": outside_corpus_range,
        "exact_page_row_count": len(exact_rows),
        "canonical_page_row_count": len(canonical_rows),
        "exact_scan_pages": sorted({row.get("scan_page") for row in exact_rows if isinstance(row.get("scan_page"), int)}),
        "canonical_scan_pages": sorted(
            {row.get("scan_page") for row in canonical_rows if isinstance(row.get("scan_page"), int)}
        ),
        "canonical_book_ids_seen": sorted({str(row.get("book_id")) for row in canonical_rows if row.get("book_id")}),
        "title_match": title_match,
        "first_line_match": first_line_match,
        "line_match_count": len(line_matches),
        "exact_line_indexes": exact_line_indexes[:20],
        "body_token_count": len(body_token_set),
        "body_token_overlap": token_overlap,
        "body_coverage": round(body_coverage, 4),
    }


def accepted_candidate_conflict(
    filename: str,
    source: dict[str, Any],
    source_book: str,
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    row = candidates.get(filename)
    if not row:
        return None
    candidate_book = canonical_book_id(str(row.get("candidate_book_id") or ""))
    if candidate_book != source_book:
        return None
    if row.get("status") != "accepted_candidate":
        return None
    if int(row.get("span_line_match_count") or 0) < 3:
        return None
    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start == source.get("page_start") and end == source.get("page_end"):
        return None
    return {
        "candidate_book_id": row.get("candidate_book_id"),
        "candidate_page_start": start,
        "candidate_page_end": end,
        "candidate_status": row.get("status"),
        "span_basis": row.get("span_basis"),
        "span_line_match_count": row.get("span_line_match_count"),
        "span_exact_line_match_count": row.get("span_exact_line_match_count"),
        "evidence": row.get("evidence"),
    }


def row_status(evidence: dict[str, Any], conflict: dict[str, Any] | None) -> str:
    if evidence["canonical_page_row_count"] == 0:
        if evidence.get("outside_corpus_range"):
            return "outside_corpus_range"
        return "missing_page_rows"
    if conflict and evidence["exact_page_row_count"] == 0:
        return "candidate_conflict"
    if conflict and not (evidence["title_match"] or evidence["line_match_count"] >= 2 or evidence["body_coverage"] >= 0.45):
        return "candidate_conflict"
    if evidence["title_match"] or evidence["first_line_match"] or evidence["line_match_count"] >= 2:
        return "supported"
    if evidence["body_coverage"] >= 0.45:
        return "supported_token_only"
    return "weak_current_citation"


def build_report(
    poems: list[tuple[str, dict[str, Any]]],
    canonical_pages: dict[tuple[str, int], list[dict[str, Any]]],
    exact_pages: dict[tuple[str, int], list[dict[str, Any]]],
    page_ranges: dict[str, dict[str, int]],
    candidates: dict[str, dict[str, Any]],
    min_line_chars: int,
) -> dict[str, Any]:
    rows = []
    skipped = Counter()
    for filename, poem in poems:
        sources = primary_printed_sources(poem)
        if not sources:
            skipped["no_primary_printed_source"] += 1
            continue
        for source in sources:
            book_id = source_book_id(source)
            if book_id is None:
                skipped["unknown_source_book"] += 1
                rows.append(
                    {
                        "filename": filename,
                        "poem_id": poem.get("id"),
                        "title_bn": poem.get("title_bn"),
                        "source_title_bn": source.get("title_bn"),
                        "status": "unknown_source_book",
                        "citation": source,
                    }
                )
                continue
            evidence = citation_evidence(
                poem,
                source,
                book_id,
                canonical_pages,
                exact_pages,
                page_ranges,
                min_line_chars=min_line_chars,
            )
            conflict = accepted_candidate_conflict(filename, source, book_id, candidates)
            rows.append(
                {
                    "filename": filename,
                    "poem_id": poem.get("id"),
                    "title_bn": poem.get("title_bn"),
                    "source_edition": poem.get("source_edition"),
                    "source_year": poem.get("source_year"),
                    "source_title_bn": source.get("title_bn"),
                    "source_book_id": book_id,
                    "status": row_status(evidence, conflict),
                    "citation": source,
                    "evidence": evidence,
                    "accepted_candidate_conflict": conflict,
                }
            )

    rows.sort(
        key=lambda row: (
            row.get("status") == "supported",
            row.get("status") == "supported_token_only",
            row.get("status") == "weak_current_citation",
            row.get("filename") or "",
        )
    )
    return {
        "summary": {
            "poem_count": len(poems),
            "citation_count": len(rows),
            "skipped": dict(skipped),
            "status_counts": dict(Counter(str(row.get("status")) for row in rows).most_common()),
            "page_ranges": page_ranges,
            "note": "Review-only report; it does not mutate poem JSON.",
        },
        "citations": rows,
    }


def markdown_report(report: dict[str, Any], max_rows: int) -> str:
    summary = report["summary"]
    rows = report["citations"]
    lines = [
        "# Citation Consistency Audit",
        "",
        "Review-only audit of existing primary printed-page citations against the repaired OCR corpus.",
        "",
        f"- Public Jibanananda poems checked: {summary['poem_count']}",
        f"- Existing primary printed-page citations checked: {summary['citation_count']}",
        f"- Status counts: `{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        "",
        "| file | title | source | status | citation | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows[:max_rows]:
        evidence = row.get("evidence") or {}
        citation = row.get("citation") or {}
        citation_label = f"{citation.get('page_start')}-{citation.get('page_end')}"
        if citation.get("page_start") == citation.get("page_end"):
            citation_label = str(citation.get("page_start"))
        evidence_label = (
            f"rows exact/canonical {evidence.get('exact_page_row_count', 0)}/"
            f"{evidence.get('canonical_page_row_count', 0)}; "
            f"corpus {evidence.get('book_corpus_page_min')}-"
            f"{evidence.get('book_corpus_page_max')}; "
            f"title {evidence.get('title_match')}; "
            f"lines {evidence.get('line_match_count', 0)}; "
            f"coverage {evidence.get('body_coverage', 0)}"
        )
        conflict = row.get("accepted_candidate_conflict")
        if conflict:
            evidence_label += (
                f"; accepted candidate {conflict.get('candidate_book_id')} "
                f"{conflict.get('candidate_page_start')}-{conflict.get('candidate_page_end')}"
            )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or "").replace("|", "\\|"),
                    str(row.get("source_title_bn") or "").replace("|", "\\|"),
                    str(row.get("status") or ""),
                    citation_label,
                    evidence_label,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit existing printed-page citations against OCR corpus evidence.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.layout.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument("--candidates", default="metadata_reports/poem-span-candidates.current.regen.jsonl")
    parser.add_argument("--ocr-substitutions", default=None)
    parser.add_argument("--output", default="metadata_reports/citation-consistency-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/citation-consistency-audit.current.md")
    parser.add_argument("--min-line-chars", type=int, default=18)
    parser.add_argument("--max-rows", type=int, default=160)
    args = parser.parse_args()

    spans.OCR_SUBSTITUTIONS = spans.load_ocr_substitutions(
        Path(args.ocr_substitutions) if args.ocr_substitutions else None
    )
    pages = read_jsonl(Path(args.page_corpus))
    page_ranges = book_page_ranges(pages)
    poems = public_jibanananda_poems(Path(args.poems_dir), duplicate_ids(Path(args.duplicates_source)))
    report = build_report(
        poems,
        canonical_pages=page_index(pages),
        exact_pages=exact_book_page_index(pages),
        page_ranges=page_ranges,
        candidates=load_candidates(Path(args.candidates)),
        min_line_chars=args.min_line_chars,
    )
    write_json(Path(args.output), report)
    markdown = markdown_report(report, args.max_rows)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
