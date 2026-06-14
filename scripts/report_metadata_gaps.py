#!/usr/bin/env python3
"""Report remaining Jibanananda metadata gaps against span candidates.

This is a review aid only. It reads poem JSON and an optional candidate JSONL,
then writes a Markdown or JSON sidecar report. It never mutates poem data.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import propose_poem_spans as spans
import embedded_source_audit
import apply_poem_metadata as apply_meta


UNKNOWN_COLLECTION = "সংকলন অজানা"
DEFAULT_REVIEW_EXCLUSIONS = "src/data/metadata-review-exclusions.json"
DEFAULT_CANDIDATES = "metadata_reports/poem-span-candidates.current.regen.jsonl"
DEFAULT_PAGE_CORPUS = "metadata_reports/page-corpus.full.repaired.layout.jsonl"
DEFAULT_TOC_AUDIT = "metadata_reports/toc-index-audit.current.json"
DEFAULT_CITATION_AUDIT = "metadata_reports/citation-consistency-audit.current.json"
SOURCE_BOOK_ID_HINTS = {
    "আলোপৃথিবী": "aloprithibi",
}
TITLE_DATE_RE = re.compile(
    r"(?:[\u09E6-\u09EF0-9]{3,4}|বৈশাখ|জ্যৈষ্ঠ|জৈষ্ঠ|আষাঢ়|আষাঢ়|শ্রাবণ|ভাদ্র|আশ্বিন|কার্তিক|অগ্রহায়ণ|অগ্রহায়ণ|পৌষ|মাঘ|ফাল্গুন|চৈত্র)"
)
TRUSTED_PAGE_TYPES = {
    "normal_poem_page",
    "poem_or_text_page",
    "poem_start_or_short_page",
}


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


def load_review_exclusions(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    data = read_json(path)
    exclusions: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("items") or []:
        filename = item.get("filename")
        if filename:
            exclusions.setdefault(str(filename), []).append(item)
    return exclusions


def duplicate_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(re.findall(r'"(jibanananda-[^"]+)"', path.read_text(encoding="utf-8")))


def has_primary_printed_pages(poem: dict[str, Any]) -> bool:
    return any(
        source.get("role") == "primary"
        and isinstance(source.get("page_start"), int)
        and isinstance(source.get("page_end"), int)
        and source.get("page_basis") == "printed_page"
        for source in poem.get("book_sources") or []
    )


def primary_printed_sources(poem: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        source
        for source in poem.get("book_sources") or []
        if source.get("role") == "primary"
        and isinstance(source.get("page_start"), int)
        and isinstance(source.get("page_end"), int)
        and source.get("page_basis") == "printed_page"
    ]


def has_primary_printed_book_year(poem: dict[str, Any]) -> bool:
    return any(isinstance(source.get("publication_year"), int) for source in primary_printed_sources(poem))


def compact(text: str) -> str:
    return re.sub(r"\s+", "", spans.normalize(text))


def normalized_body_lines(body: str, min_chars: int) -> list[dict[str, Any]]:
    lines = []
    for line_index, raw_line in enumerate((body or "").splitlines()):
        normalized = spans.normalize(raw_line)
        if len(compact(normalized)) < min_chars:
            continue
        lines.append({"line_index": line_index, "raw": raw_line.strip(), "normalized": normalized})
    return lines


def load_public_jibanananda_poems(poems_dir: Path, duplicates: set[str]) -> list[tuple[str, dict[str, Any]]]:
    poems = []
    for path in sorted(glob.glob(str(poems_dir / "*.json"))):
        poem = read_json(Path(path))
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if poem.get("id") in duplicates:
            continue
        poems.append((Path(path).name, poem))
    return poems


def load_source_pages(path: Path | None, trusted_only: bool) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    pages_by_book: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl(path):
        if trusted_only and row.get("page_type") not in TRUSTED_PAGE_TYPES:
            continue
        prepared = spans.prepare_page(row)
        pages_by_book.setdefault(str(prepared.get("book_id") or ""), []).append(prepared)
    return pages_by_book


def source_scan_review(
    poem: dict[str, Any],
    pages_by_book: dict[str, list[dict[str, Any]]],
    include_logical_aliases: bool,
    min_line_chars: int,
) -> dict[str, Any] | None:
    edition = poem.get("source_edition")
    if edition == UNKNOWN_COLLECTION:
        return None
    if edition not in spans.COLLECTION_TO_BOOK_ID:
        return {
            "status": "unscanned_source_edition",
            "source_edition": edition,
            "reason": "source edition is not mapped to a current OCR book corpus",
        }

    allowed_books = spans.collection_book_ids(str(edition), include_logical_aliases=include_logical_aliases)
    source_pages = [page for book_id in allowed_books for page in pages_by_book.get(book_id, [])]
    if not source_pages:
        return {
            "status": "no_source_scan_pages",
            "source_edition": edition,
            "candidate_book_ids": sorted(allowed_books),
        }

    title = spans.normalize(str(poem.get("title_bn") or ""))
    body = str(poem.get("body_bn") or "")
    body_tokens = spans.tokens(body)
    body_lines = normalized_body_lines(body, min_chars=min_line_chars)
    best: dict[str, Any] | None = None
    for page in source_pages:
        page_text = spans.page_text(page)
        page_tokens = page.get("_tokens") or spans.tokens(page_text)
        exact_lines = [
            line
            for line in body_lines
            if line["normalized"] and line["normalized"] in page_text
        ]
        title_match = bool(title and (spans.title_heading_match(title, page) or title in page_text))
        first_line_match = bool(body_lines and body_lines[0]["normalized"] in page_text)
        token_overlap = len(body_tokens & page_tokens)
        body_coverage = token_overlap / max(1, len(body_tokens))
        row = {
            "candidate_book_id": page.get("book_id"),
            "candidate_scan_page": page.get("scan_page"),
            "candidate_printed_page": page.get("printed_page_fixed"),
            "candidate_page_type": page.get("page_type"),
            "title_match": title_match,
            "first_line_match": first_line_match,
            "line_match_count": len(exact_lines),
            "exact_line_indexes": [line["line_index"] for line in exact_lines[:12]],
            "body_token_count": len(body_tokens),
            "body_token_overlap": token_overlap,
            "body_coverage": round(body_coverage, 4),
        }
        if best is None or (
            int(row["line_match_count"]),
            bool(row["title_match"]),
            bool(row["first_line_match"]),
            float(row["body_coverage"]),
            int(row["body_token_overlap"]),
        ) > (
            int(best["line_match_count"]),
            bool(best["title_match"]),
            bool(best["first_line_match"]),
            float(best["body_coverage"]),
            int(best["body_token_overlap"]),
        ):
            best = row

    assert best is not None
    if best["title_match"] or best["first_line_match"] or int(best["line_match_count"]) >= 2:
        status = "source_scan_supported"
    elif float(best["body_coverage"]) >= 0.35:
        status = "source_scan_token_only"
    elif float(best["body_coverage"]) >= 0.18:
        status = "source_scan_weak"
    else:
        status = "source_scan_no_support"
    return {
        "status": status,
        "source_edition": edition,
        "candidate_book_ids": sorted(allowed_books),
        "best_page": best,
    }


def evidence_score(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    score = 0
    if isinstance(row.get("printed_page_start"), int) and isinstance(row.get("printed_page_end"), int):
        score += 3
    if row.get("span_basis") == "line_anchor_cluster":
        score += 3
    score += min(int(row.get("span_exact_line_match_count") or 0), 10)
    score += min(int(row.get("span_line_match_count") or 0), 10) // 2
    evidence = set(row.get("evidence") or [])
    if "title_match" in evidence:
        score += 2
    if "first_line_match" in evidence:
        score += 2
    if "last_line_match" in evidence:
        score += 2
    if "high_body_coverage" in evidence:
        score += 2
    return score


def matches_review_exclusion(row: dict[str, Any] | None, exclusion: dict[str, Any]) -> bool:
    expected_book = exclusion.get("candidate_book_id")
    if expected_book != (row or {}).get("candidate_book_id"):
        return False

    for exclusion_key, row_key in (
        ("candidate_page_start", "printed_page_start"),
        ("candidate_page_end", "printed_page_end"),
    ):
        if exclusion_key in exclusion and exclusion.get(exclusion_key) != (row or {}).get(row_key):
            return False

    return True


def find_review_exclusion(
    filename: str,
    row: dict[str, Any] | None,
    review_exclusions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    for exclusion in review_exclusions.get(filename, []):
        if matches_review_exclusion(row, exclusion):
            return exclusion
    return None


def embedded_source_conflict(filename: str, poem: dict[str, Any]) -> dict[str, Any] | None:
    marker = embedded_source_audit.find_marker(str(poem.get("body_bn") or ""))
    if marker is None:
        return None
    current = poem.get("source_edition")
    marker_source = marker.get("source_edition")
    if current in {None, UNKNOWN_COLLECTION, marker_source}:
        return None
    return {
        "filename": filename,
        "poem_id": poem.get("id"),
        "title_bn": poem.get("title_bn"),
        "current_source_edition": current,
        "current_source_year": poem.get("source_year"),
        "marker_source_edition": marker_source,
        "marker_source_year": marker.get("source_year"),
        "marker_phase_id": marker.get("phase_id"),
        "marker_line": marker.get("line"),
        "source_url": poem.get("source_url"),
    }


def review_bucket(
    poem: dict[str, Any],
    row: dict[str, Any] | None,
    marker_conflict: dict[str, Any] | None = None,
) -> str:
    if marker_conflict is not None:
        return "conflicting_embedded_source_marker"
    if row is None or row.get("status") == "no_candidate":
        return "no_candidate"
    if not isinstance(row.get("printed_page_start"), int) or not isinstance(row.get("printed_page_end"), int):
        return "needs_printed_page_sequence"
    if row.get("span_basis") != "line_anchor_cluster":
        return "token_or_title_only_candidate"
    if int(row.get("span_anchor_count") or 0) == 0:
        return "token_or_title_only_candidate"
    if int(row.get("span_exact_line_match_count") or 0) >= 3:
        if poem.get("source_edition") == UNKNOWN_COLLECTION:
            return "manual_collection_review"
        return "manual_page_review"
    return "weak_text_anchor"


def candidate_summary(row: dict[str, Any] | None) -> str:
    if row is None or row.get("status") == "no_candidate":
        return "-"
    page_start = row.get("printed_page_start")
    page_end = row.get("printed_page_end")
    if isinstance(page_start, int) and isinstance(page_end, int):
        page_label = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
    else:
        page_label = "পৃষ্ঠা নেই"
    pieces = [
        str(row.get("candidate_book_id") or "-"),
        page_label,
        str(row.get("status") or "-"),
        f"score {row.get('score')}",
    ]
    if row.get("runner_up_gap") is not None:
        pieces.append(f"gap {row.get('runner_up_gap')}")
    line_count = row.get("span_line_match_count")
    exact_count = row.get("span_exact_line_match_count")
    if line_count is not None or exact_count is not None:
        pieces.append(f"lines {line_count or 0}/{exact_count or 0} exact")
    return "; ".join(pieces)


def source_scan_summary(review: dict[str, Any] | None) -> str:
    if not review:
        return "-"
    status = str(review.get("status") or "")
    best = review.get("best_page") or {}
    if not best:
        return status
    page = best.get("candidate_printed_page")
    page_label = str(page) if isinstance(page, int) else "পৃষ্ঠা নেই"
    return (
        f"{status}; {best.get('candidate_book_id')}; p.{page_label}; "
        f"coverage {best.get('body_coverage')}; "
        f"lines {best.get('line_match_count')}; title {best.get('title_match')}"
    )


def review_note_summary(item: dict[str, Any]) -> str:
    exclusion = item.get("review_exclusion")
    if not exclusion:
        return ""
    return str(exclusion.get("note_bn") or exclusion.get("reason") or "")


def source_coverage_blockers(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in missing:
        edition = item.get("source_edition") or ""
        if not edition or edition == UNKNOWN_COLLECTION:
            continue
        review = item.get("source_scan_review") or {}
        status = str(review.get("status") or "not_applicable")
        if status in {"source_scan_supported", "source_scan_token_only"}:
            continue
        key = (status, edition)
        row = grouped.setdefault(
            key,
            {
                "status": status,
                "source_edition": edition,
                "count": 0,
                "candidate_book_ids": sorted(review.get("candidate_book_ids") or []),
                "items": [],
            },
        )
        row["count"] += 1
        row["items"].append(
            {
                "filename": item.get("filename"),
                "poem_id": item.get("poem_id"),
                "title_bn": item.get("title_bn"),
                "source_year": item.get("source_year"),
                "review_bucket": item.get("review_bucket"),
                "source_url": item.get("source_url"),
            }
        )
    return sorted(
        grouped.values(),
        key=lambda row: (
            row["status"] != "unscanned_source_edition",
            row["source_edition"],
            -int(row["count"]),
        ),
    )


def book_title_from_id(book_id: str | None) -> str | None:
    if not book_id:
        return None
    canonical = apply_meta.BOOK_ALIASES.get(book_id, book_id)
    meta = apply_meta.BASE_BOOK_META.get(canonical)
    if meta:
        return str(meta.get("title_bn") or "")
    for title, mapped_book_id in spans.COLLECTION_TO_BOOK_ID.items():
        if mapped_book_id == canonical:
            return title
    return None


def source_book_id_hint(source_edition: str | None) -> str | None:
    if not source_edition:
        return None
    return spans.COLLECTION_TO_BOOK_ID.get(source_edition) or SOURCE_BOOK_ID_HINTS.get(source_edition)


def source_corpus_backlog(
    missing: list[dict[str, Any]],
    citation_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Group remaining scan/corpus blockers across missing and existing citations."""

    rows: list[dict[str, Any]] = []

    existing_groups: dict[str, dict[str, Any]] = {}
    for item in (citation_audit or {}).get("citations") or []:
        if item.get("status") != "missing_book_corpus":
            continue
        book_id = str(item.get("source_book_id") or "")
        row = existing_groups.setdefault(
            book_id,
            {
                "kind": "missing_corpus_for_existing_citations",
                "action": "add_corpus_or_regenerate_auxiliary_corpus",
                "source_edition": item.get("source_edition") or item.get("source_title_bn") or book_title_from_id(book_id),
                "book_id": book_id,
                "count": 0,
                "items": [],
            },
        )
        row["count"] += 1
        row["items"].append(
            {
                "filename": item.get("filename"),
                "poem_id": item.get("poem_id"),
                "title_bn": item.get("title_bn"),
                "page_start": (item.get("citation") or {}).get("page_start"),
                "page_end": (item.get("citation") or {}).get("page_end"),
            }
        )
    rows.extend(existing_groups.values())

    unscanned_groups: dict[str, dict[str, Any]] = {}
    weak_groups: dict[tuple[str, str], dict[str, Any]] = {}
    reviewed_scan_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    unknown_items = []
    for item in missing:
        edition = str(item.get("source_edition") or "")
        if edition == UNKNOWN_COLLECTION:
            if len(unknown_items) < 12:
                unknown_items.append(
                    {
                        "filename": item.get("filename"),
                        "poem_id": item.get("poem_id"),
                        "title_bn": item.get("title_bn"),
                        "source_url": item.get("source_url"),
                    }
                )
            continue

        review = item.get("source_scan_review") or {}
        status = str(review.get("status") or "")
        if status in {"unscanned_source_edition", "no_source_scan_pages"}:
            row = unscanned_groups.setdefault(
                edition,
                {
                    "kind": "missing_source_corpus_for_uncited_poems",
                    "action": "add_scan_or_direct_print_review",
                    "source_edition": edition,
                    "book_id": source_book_id_hint(edition),
                    "status": status,
                    "count": 0,
                    "items": [],
                },
            )
            row["count"] += 1
            row["items"].append(
                {
                    "filename": item.get("filename"),
                    "poem_id": item.get("poem_id"),
                    "title_bn": item.get("title_bn"),
                    "source_url": item.get("source_url"),
                }
            )
        elif status in {"source_scan_weak", "source_scan_no_support"}:
            review_bucket = str(item.get("review_bucket") or "")
            if review_bucket.startswith("reviewed_"):
                key = (edition, status, review_bucket)
                row = reviewed_scan_groups.setdefault(
                    key,
                    {
                        "kind": "reviewed_scanned_source_gap",
                        "action": "keep_excluded_until_new_scan_or_print_review",
                        "source_edition": edition,
                        "book_id": source_book_id_hint(edition),
                        "status": status,
                        "review_bucket": review_bucket,
                        "count": 0,
                        "items": [],
                    },
                )
                row["count"] += 1
                row["items"].append(
                    {
                        "filename": item.get("filename"),
                        "poem_id": item.get("poem_id"),
                        "title_bn": item.get("title_bn"),
                        "source_url": item.get("source_url"),
                        "source_scan": source_scan_summary(review),
                    }
                )
                continue
            key = (edition, status)
            row = weak_groups.setdefault(
                key,
                {
                    "kind": "scanned_source_needs_better_extraction",
                    "action": "improve_text_extraction_or_manual_page_review",
                    "source_edition": edition,
                    "book_id": source_book_id_hint(edition),
                    "status": status,
                    "count": 0,
                    "items": [],
                },
            )
            row["count"] += 1
            row["items"].append(
                {
                    "filename": item.get("filename"),
                    "poem_id": item.get("poem_id"),
                    "title_bn": item.get("title_bn"),
                    "source_url": item.get("source_url"),
                    "source_scan": source_scan_summary(review),
                }
            )

    rows.extend(unscanned_groups.values())
    rows.extend(reviewed_scan_groups.values())
    rows.extend(weak_groups.values())
    unknown_count = sum(1 for item in missing if item.get("source_edition") == UNKNOWN_COLLECTION)
    if unknown_count:
        rows.append(
            {
                "kind": "unknown_source_collection",
                "action": "identify_collection_before_page_citation",
                "source_edition": UNKNOWN_COLLECTION,
                "book_id": None,
                "count": unknown_count,
                "items": unknown_items,
            }
        )

    priority = {
        "missing_corpus_for_existing_citations": 0,
        "missing_source_corpus_for_uncited_poems": 1,
        "scanned_source_needs_better_extraction": 2,
        "reviewed_scanned_source_gap": 3,
        "unknown_source_collection": 4,
    }
    return sorted(rows, key=lambda row: (priority.get(str(row["kind"]), 99), -int(row["count"]), row.get("source_edition") or ""))


def matches_toc_review_exclusion(match: dict[str, Any], exclusion: dict[str, Any]) -> bool:
    candidate = match.get("best_candidate") or {}
    expected_book = exclusion.get("candidate_book_id")
    if expected_book != candidate.get("candidate_book_id"):
        return False

    printed_page = candidate.get("printed_page")
    for exclusion_key in ("candidate_page_start", "candidate_page_end"):
        if exclusion_key in exclusion and exclusion.get(exclusion_key) != printed_page:
            return False

    return True


def find_toc_review_exclusion(
    filename: str,
    match: dict[str, Any],
    review_exclusions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    for exclusion in review_exclusions.get(filename, []):
        if matches_toc_review_exclusion(match, exclusion):
            return exclusion
    return None


def toc_index_blockers(
    toc_matches_by_file: dict[str, dict[str, Any]],
    review_exclusions: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for filename, match in sorted(toc_matches_by_file.items()):
        candidate = match.get("best_candidate") or {}
        review_exclusion = find_toc_review_exclusion(filename, match, review_exclusions)
        rows.append(
            {
                "filename": filename,
                "poem_id": match.get("poem_id"),
                "title_bn": match.get("title_bn"),
                "source_edition": match.get("source_edition"),
                "status": match.get("status"),
                "candidate_book_id": candidate.get("candidate_book_id"),
                "candidate_collection_bn": candidate.get("candidate_collection_bn"),
                "printed_page": candidate.get("printed_page"),
                "candidate_scan_page": candidate.get("candidate_scan_page"),
                "entry_title": candidate.get("entry_title"),
                "match_basis": candidate.get("match_basis"),
                "title_score": candidate.get("title_score"),
                "toc_line": candidate.get("toc_line"),
                "duplicate_title_context": match.get("duplicate_title_context") or [],
                "review_exclusion": review_exclusion,
            }
        )
    return rows


def source_year_blockers(poems: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for filename, poem in poems:
        if poem.get("source_year") is not None:
            continue
        primary_sources = primary_printed_sources(poem)
        edition = str(poem.get("source_edition") or "")
        row = grouped.setdefault(
            edition,
            {
                "source_edition": edition,
                "count": 0,
                "items": [],
            },
        )
        row["count"] += 1
        row["items"].append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_url": poem.get("source_url"),
                "primary_printed_sources": primary_sources,
                "has_primary_printed_book_year": has_primary_printed_book_year(poem),
            }
        )
    return sorted(grouped.values(), key=lambda row: (-int(row["count"]), row["source_edition"]))


def primary_printed_book_year_blockers(poems: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for filename, poem in poems:
        if has_primary_printed_book_year(poem):
            continue
        edition = str(poem.get("source_edition") or "")
        row = grouped.setdefault(
            edition,
            {
                "source_edition": edition,
                "count": 0,
                "items": [],
            },
        )
        row["count"] += 1
        if len(row["items"]) < 12:
            row["items"].append(
                {
                    "filename": filename,
                    "poem_id": poem.get("id"),
                    "title_bn": poem.get("title_bn"),
                    "source_year": poem.get("source_year"),
                    "has_primary_printed_pages": has_primary_printed_pages(poem),
                }
            )
    return sorted(grouped.values(), key=lambda row: (-int(row["count"]), row["source_edition"]))


def title_date_candidates(poems: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for filename, poem in poems:
        title = str(poem.get("title_bn") or "")
        matches = TITLE_DATE_RE.findall(title)
        if not matches:
            continue
        rows.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": title,
                "source_edition": poem.get("source_edition"),
                "source_year": poem.get("source_year"),
                "composition_date_bn": poem.get("composition_date_bn"),
                "title_date_fragments": matches,
                "source_url": poem.get("source_url"),
            }
        )
    return rows


def composition_date_blockers(poems: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for filename, poem in poems:
        if poem.get("composition_date_bn"):
            continue
        edition = str(poem.get("source_edition") or "")
        row = grouped.setdefault(
            edition,
            {
                "source_edition": edition,
                "count": 0,
                "items": [],
            },
        )
        row["count"] += 1
        if len(row["items"]) < 12:
            row["items"].append(
                {
                    "filename": filename,
                    "poem_id": poem.get("id"),
                    "title_bn": poem.get("title_bn"),
                    "source_year": poem.get("source_year"),
                }
            )
    return sorted(grouped.values(), key=lambda row: (-int(row["count"]), row["source_edition"]))


def build_report(
    poems: list[tuple[str, dict[str, Any]]],
    candidates_by_file: dict[str, dict[str, Any]],
    review_exclusions: dict[str, list[dict[str, Any]]],
    pages_by_book: dict[str, list[dict[str, Any]]],
    toc_matches_by_file: dict[str, dict[str, Any]],
    citation_audit: dict[str, Any] | None,
    include_logical_aliases: bool,
    source_scan_min_line_chars: int,
) -> dict[str, Any]:
    missing = []
    marker_conflicts = [
        conflict
        for filename, poem in poems
        if (conflict := embedded_source_conflict(filename, poem)) is not None
    ]
    marker_conflicts_by_file = {str(item["filename"]): item for item in marker_conflicts}
    for filename, poem in poems:
        if has_primary_printed_pages(poem):
            continue
        row = candidates_by_file.get(filename)
        review_exclusion = find_review_exclusion(filename, row, review_exclusions)
        marker_conflict = marker_conflicts_by_file.get(filename)
        bucket = (
            str(review_exclusion.get("review_bucket"))
            if review_exclusion
            else review_bucket(poem, row, marker_conflict=marker_conflict)
        )
        missing.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_edition": poem.get("source_edition"),
                "source_year": poem.get("source_year"),
                "source_name_bn": poem.get("source_name_bn"),
                "source_url": poem.get("source_url"),
                "review_bucket": bucket,
                "evidence_score": evidence_score(row),
                "candidate": row,
                "source_scan_review": source_scan_review(
                    poem,
                    pages_by_book,
                    include_logical_aliases=include_logical_aliases,
                    min_line_chars=source_scan_min_line_chars,
                ),
                "review_exclusion": review_exclusion,
                "embedded_source_conflict": marker_conflict,
                "toc_index_candidate": toc_matches_by_file.get(filename),
            }
        )

    missing.sort(
        key=lambda item: (
            item["review_bucket"].startswith("reviewed_"),
            item["review_bucket"] in {"no_candidate", "token_or_title_only_candidate"},
            -int(item["evidence_score"]),
            item["source_edition"] or "",
            item["title_bn"] or "",
        )
    )
    blockers = source_coverage_blockers(missing)
    toc_rows = toc_index_blockers(toc_matches_by_file, review_exclusions)
    toc_blockers = [item for item in toc_rows if not item.get("review_exclusion")]
    toc_reviewed_exclusions = [item for item in toc_rows if item.get("review_exclusion")]
    source_year_missing = source_year_blockers(poems)
    primary_book_year_missing = primary_printed_book_year_blockers(poems)
    title_date_rows = title_date_candidates(poems)
    composition_date_missing = composition_date_blockers(poems)
    corpus_backlog = source_corpus_backlog(missing, citation_audit)
    corpus_backlog_action_counts: Counter[str] = Counter()
    corpus_backlog_action_group_counts: Counter[str] = Counter()
    for item in corpus_backlog:
        action = str(item["action"])
        corpus_backlog_action_counts[action] += int(item["count"])
        corpus_backlog_action_group_counts[action] += 1
    return {
        "summary": {
            "public_poem_count": len(poems),
            "missing_printed_page_count": len(missing),
            "unknown_collection_missing_count": sum(
                1 for item in missing if item["source_edition"] == UNKNOWN_COLLECTION
            ),
            "missing_source_year_count": sum(1 for _, poem in poems if poem.get("source_year") is None),
            "missing_primary_printed_book_year_count": sum(
                1 for _, poem in poems if not has_primary_printed_book_year(poem)
            ),
            "missing_composition_date_count": sum(1 for _, poem in poems if not poem.get("composition_date_bn")),
            "review_buckets": dict(Counter(item["review_bucket"] for item in missing).most_common()),
            "source_scan_status_counts": dict(
                Counter(
                    (item.get("source_scan_review") or {}).get("status") or "not_applicable"
                    for item in missing
                ).most_common()
            ),
            "missing_by_source_edition": dict(
                Counter(item["source_edition"] or "" for item in missing).most_common()
            ),
            "reviewed_exclusion_count": sum(1 for item in missing if item.get("review_exclusion")),
            "embedded_source_conflict_count": len(marker_conflicts),
            "missing_embedded_source_conflict_count": sum(
                1 for item in missing if item.get("embedded_source_conflict")
            ),
            "source_coverage_blocker_count": sum(int(item["count"]) for item in blockers),
            "source_coverage_blocker_groups": len(blockers),
            "source_corpus_backlog_count": sum(int(item["count"]) for item in corpus_backlog),
            "source_corpus_backlog_groups": len(corpus_backlog),
            "source_corpus_backlog_action_counts": dict(corpus_backlog_action_counts.most_common()),
            "source_corpus_backlog_action_group_counts": dict(corpus_backlog_action_group_counts.most_common()),
            "toc_index_candidate_count": len(toc_blockers),
            "toc_index_reviewed_exclusion_count": len(toc_reviewed_exclusions),
            "toc_index_status_counts": dict(Counter(str(item.get("status") or "") for item in toc_blockers).most_common()),
            "title_date_candidate_count": len(title_date_rows),
            "title_date_source_year_gap_count": sum(1 for item in title_date_rows if item.get("source_year") is None),
        },
        "source_corpus_backlog": corpus_backlog,
        "source_coverage_blockers": blockers,
        "toc_index_blockers": toc_blockers,
        "toc_index_reviewed_exclusions": toc_reviewed_exclusions,
        "source_year_blockers": source_year_missing,
        "primary_printed_book_year_blockers": primary_book_year_missing,
        "title_date_candidates": title_date_rows,
        "composition_date_blockers": composition_date_missing,
        "embedded_source_conflicts": marker_conflicts,
        "missing": missing,
    }


def markdown_report(report: dict[str, Any], max_rows: int) -> str:
    summary = report["summary"]
    lines = [
        "# Jibanananda metadata gap review",
        "",
        "Generated from current poem JSON and span-candidate sidecars.",
        "",
        "## Summary",
        "",
        f"- Public Jibanananda poems: {summary['public_poem_count']}",
        f"- Missing printed page citations: {summary['missing_printed_page_count']}",
        f"- Missing citations with unknown collection: {summary['unknown_collection_missing_count']}",
        f"- Public poems missing source year: {summary['missing_source_year_count']}",
        f"- Public poems missing primary printed book year: {summary['missing_primary_printed_book_year_count']}",
        f"- Public poems missing composition date: {summary['missing_composition_date_count']}",
        f"- Source corpus backlog records: {summary['source_corpus_backlog_count']} in {summary['source_corpus_backlog_groups']} groups",
        f"- TOC index candidates requiring review: {summary['toc_index_candidate_count']}",
        f"- Reviewed TOC exclusions: {summary['toc_index_reviewed_exclusion_count']}",
        f"- Title date candidates requiring printed-source review: {summary['title_date_candidate_count']}",
        f"- Reviewed exclusions: {summary['reviewed_exclusion_count']}",
        f"- Embedded source marker conflicts: {summary['embedded_source_conflict_count']}",
        "",
        "## Source Scan Status",
        "",
    ]
    for status, count in summary["source_scan_status_counts"].items():
        lines.append(f"- `{status}`: {count}")

    lines.extend(
        [
            "",
            "## Review buckets",
            "",
        ]
    )
    for bucket, count in summary["review_buckets"].items():
        lines.append(f"- `{bucket}`: {count}")

    lines.extend(
        [
            "",
            "## Missing By Source Edition",
            "",
        ]
    )
    for edition, count in summary["missing_by_source_edition"].items():
        lines.append(f"- {edition}: {count}")

    corpus_backlog = report.get("source_corpus_backlog") or []
    lines.extend(
        [
            "",
            "## Source Corpus Backlog",
            "",
            "This combines missing corpora for already-cited poems, missing scans for uncited known-source poems, weak scanned-source evidence, and unknown-source rows. It is the acquisition/review queue before more page citations can be added safely.",
            "",
        ]
    )
    if corpus_backlog:
        lines.extend(
            [
                "| kind | action | source edition | book id | count | sample titles |",
                "|---|---|---|---|---:|---|",
            ]
        )
        for row in corpus_backlog:
            titles = " · ".join(str(item.get("title_bn") or "") for item in (row.get("items") or [])[:8])
            remaining = int(row.get("count") or 0) - min(int(row.get("count") or 0), 8)
            if remaining > 0:
                titles = f"{titles} · +{remaining} more"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("kind") or ""),
                        str(row.get("action") or ""),
                        str(row.get("source_edition") or ""),
                        str(row.get("book_id") or ""),
                        str(row.get("count") or 0),
                        titles.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    toc_blockers = report.get("toc_index_blockers") or []
    lines.extend(
        [
            "",
            "## TOC Index Blockers",
            "",
            "TOC title/page hits remain review-only. Duplicate-title rows cannot receive page citations without body-text support.",
            "",
        ]
    )
    if toc_blockers:
        lines.extend(["| file | title | status | candidate | duplicate context |", "|---|---|---|---|---|"])
        for item in toc_blockers:
            duplicate_context = " · ".join(
                f"{ctx.get('poem_id')} {ctx.get('source_edition') or ''}".strip()
                for ctx in item.get("duplicate_title_context") or []
            )
            candidate = (
                f"{item.get('candidate_book_id')}; p.{item.get('printed_page')}; "
                f"{item.get('entry_title')}; score {item.get('title_score')}"
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("filename") or ""),
                        str(item.get("title_bn") or ""),
                        f"`{item.get('status') or ''}`",
                        candidate.replace("|", "\\|"),
                        duplicate_context.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    year_blockers = report.get("source_year_blockers") or []
    lines.extend(
        [
            "",
            "## Source Year Blockers",
            "",
        ]
    )
    if year_blockers:
        lines.extend(["| source edition | count | titles |", "|---|---:|---|"])
        for blocker in year_blockers:
            titles = " · ".join(str(item.get("title_bn") or "") for item in (blocker.get("items") or [])[:8])
            remaining = int(blocker.get("count") or 0) - min(int(blocker.get("count") or 0), 8)
            if remaining > 0:
                titles = f"{titles} · +{remaining} more"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(blocker.get("source_edition") or ""),
                        str(blocker.get("count") or 0),
                        titles.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    primary_book_year_blockers = report.get("primary_printed_book_year_blockers") or []
    lines.extend(
        [
            "",
            "## Primary Printed Book Year Blockers",
            "",
            "This counts cited-book publication-year coverage, separate from editorial `source_year`.",
            "",
        ]
    )
    if primary_book_year_blockers:
        lines.extend(["| source edition | count | sample titles |", "|---|---:|---|"])
        for blocker in primary_book_year_blockers:
            titles = " · ".join(str(item.get("title_bn") or "") for item in (blocker.get("items") or [])[:8])
            remaining = int(blocker.get("count") or 0) - min(int(blocker.get("count") or 0), 8)
            if remaining > 0:
                titles = f"{titles} · +{remaining} more"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(blocker.get("source_edition") or ""),
                        str(blocker.get("count") or 0),
                        titles.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    title_dates = report.get("title_date_candidates") or []
    lines.extend(
        [
            "",
            "## Title Date Candidates",
            "",
            "Date-like text in a title is only a review cue; it is not enough to fill source year or composition date without printed-source support.",
            "",
        ]
    )
    if title_dates:
        lines.extend(["| file | title | current source | fragments | source URL |", "|---|---|---|---|---|"])
        for item in title_dates:
            source_year = item.get("source_year") if item.get("source_year") is not None else ""
            source = f"{item.get('source_edition') or ''} {source_year}".strip()
            fragments = ", ".join(str(fragment) for fragment in item.get("title_date_fragments") or [])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("filename") or ""),
                        str(item.get("title_bn") or ""),
                        source,
                        fragments,
                        str(item.get("source_url") or ""),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    composition_blockers = report.get("composition_date_blockers") or []
    lines.extend(
        [
            "",
            "## Composition Date Blockers",
            "",
            "Composition dates are absent until the printed-source date audit can verify authorial date/place signatures.",
            "",
        ]
    )
    if composition_blockers:
        lines.extend(["| source edition | count | sample titles |", "|---|---:|---|"])
        for blocker in composition_blockers:
            titles = " · ".join(str(item.get("title_bn") or "") for item in blocker.get("items") or [])
            remaining = int(blocker.get("count") or 0) - len(blocker.get("items") or [])
            if remaining > 0:
                titles = f"{titles} · +{remaining} more"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(blocker.get("source_edition") or ""),
                        str(blocker.get("count") or 0),
                        titles.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    blockers = report.get("source_coverage_blockers") or []
    lines.extend(
        [
            "",
            "## Source Coverage Blockers",
            "",
        ]
    )
    if blockers:
        lines.extend(
            [
                "| status | source edition | count | titles |",
                "|---|---|---:|---|",
            ]
        )
        for blocker in blockers:
            titles = " · ".join(
                str(item.get("title_bn") or "")
                for item in (blocker.get("items") or [])[:8]
            )
            remaining = int(blocker.get("count") or 0) - min(int(blocker.get("count") or 0), 8)
            if remaining > 0:
                titles = f"{titles} · +{remaining} more"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(blocker.get("status") or ""),
                        str(blocker.get("source_edition") or ""),
                        str(blocker.get("count") or 0),
                        titles.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    marker_conflicts = report.get("embedded_source_conflicts") or []
    lines.extend(
        [
            "",
            "## Embedded Source Marker Conflicts",
            "",
        ]
    )
    if marker_conflicts:
        lines.extend(
            [
                "| file | title | current source | marker source | marker line |",
                "|---|---|---|---|---|",
            ]
        )
        for conflict in marker_conflicts:
            marker_line = str(conflict.get("marker_line") or "").replace("|", "\\|")
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(conflict.get("filename") or ""),
                        str(conflict.get("title_bn") or ""),
                        str(conflict.get("current_source_edition") or ""),
                        str(conflict.get("marker_source_edition") or ""),
                        marker_line,
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Ranked Rows",
            "",
            "| file | title | current source | review bucket | candidate | source scan | source URL |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in report["missing"][:max_rows]:
        source_year = item["source_year"] if item["source_year"] is not None else ""
        source = f"{item['source_edition']} {source_year}".strip()
        source_url = item.get("source_url") or ""
        if item.get("review_exclusion"):
            source_url = f"{source_url}<br>{review_note_summary(item)}" if source_url else review_note_summary(item)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["filename"]),
                    str(item["title_bn"]),
                    source,
                    f"`{item['review_bucket']}`",
                    candidate_summary(item.get("candidate")),
                    source_scan_summary(item.get("source_scan_review")),
                    source_url,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report remaining poem metadata gaps.")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument(
        "--candidates",
        default=DEFAULT_CANDIDATES,
        help="Span-candidate JSONL used to explain remaining gaps. Use an empty string to disable.",
    )
    parser.add_argument(
        "--page-corpus",
        default=DEFAULT_PAGE_CORPUS,
        help="Repaired page corpus used for source-scan status. Use an empty string to disable.",
    )
    parser.add_argument(
        "--toc-audit",
        default=DEFAULT_TOC_AUDIT,
        help="Optional TOC index audit JSON to surface title/page blockers. Use an empty string to disable.",
    )
    parser.add_argument(
        "--citation-audit",
        default=DEFAULT_CITATION_AUDIT,
        help="Optional citation consistency audit JSON to include missing existing-source corpora. Use an empty string to disable.",
    )
    parser.add_argument("--ocr-substitutions", default=None)
    parser.add_argument("--include-logical-aliases", action="store_true")
    parser.add_argument("--include-untrusted-pages", action="store_true")
    parser.add_argument("--source-scan-min-line-chars", type=int, default=18)
    parser.add_argument("--review-exclusions", default=DEFAULT_REVIEW_EXCLUSIONS)
    parser.add_argument("--output", default="metadata_reports/metadata-gap-review.current.md")
    parser.add_argument("--json-output", default="metadata_reports/metadata-gap-review.current.json")
    parser.add_argument("--max-rows", type=int, default=140)
    args = parser.parse_args()

    duplicates = duplicate_ids(Path(args.duplicates_source))
    spans.OCR_SUBSTITUTIONS = spans.load_ocr_substitutions(
        Path(args.ocr_substitutions) if args.ocr_substitutions else None
    )
    poems = load_public_jibanananda_poems(Path(args.poems_dir), duplicates)
    pages_by_book = load_source_pages(
        Path(args.page_corpus) if args.page_corpus else None,
        trusted_only=not args.include_untrusted_pages,
    )
    candidates_by_file: dict[str, dict[str, Any]] = {}
    if args.candidates:
        for row in read_jsonl(Path(args.candidates)):
            filename = row.get("filename")
            if filename:
                candidates_by_file[str(filename)] = row
    toc_matches_by_file: dict[str, dict[str, Any]] = {}
    if args.toc_audit and Path(args.toc_audit).exists():
        for row in read_json(Path(args.toc_audit)).get("matches") or []:
            filename = row.get("filename")
            if filename:
                toc_matches_by_file[str(filename)] = row
    citation_audit = read_json(Path(args.citation_audit)) if args.citation_audit and Path(args.citation_audit).exists() else None
    review_exclusions = load_review_exclusions(Path(args.review_exclusions))

    report = build_report(
        poems,
        candidates_by_file,
        review_exclusions,
        pages_by_book,
        toc_matches_by_file,
        citation_audit,
        include_logical_aliases=args.include_logical_aliases,
        source_scan_min_line_chars=args.source_scan_min_line_chars,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown_report(report, args.max_rows), encoding="utf-8")

    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
