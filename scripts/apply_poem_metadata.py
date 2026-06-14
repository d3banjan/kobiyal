#!/usr/bin/env python3
"""Apply gated poem metadata from span candidates to poem JSON files.

This script is intentionally conservative. It only applies page citations when
the span candidate is accepted, has printed book page numbers, and does not
contradict an existing known collection assignment.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


BASE_BOOK_META = {
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

BOOK_ALIASES = {
    "dhusar-pandulipi-copy": "dhusar-pandulipi",
    "banalata-sen-appendix": "banalata-sen",
    "mahaprithibi-copy": "mahaprithibi",
    "mahaprithibi-appendix-copy": "mahaprithibi",
    "rupasi-bangla-copy": "rupasi-bangla",
    "bela-abela-kalabela-copy": "bela-abela-kalabela",
}

BOOK_META = dict(BASE_BOOK_META)
for alias, canonical in BOOK_ALIASES.items():
    BOOK_META[alias] = BASE_BOOK_META[canonical]

UNKNOWN_COLLECTION = "সংকলন অজানা"
DEFAULT_DUPLICATES_SOURCE = "src/lib/content.ts"


def canonical_book_id(book_id: str | None) -> str | None:
    if not book_id:
        return None
    return BOOK_ALIASES.get(book_id, book_id)


def source_title_book_id(title_bn: str | None) -> str | None:
    if not title_bn:
        return None
    for book_id, meta in BASE_BOOK_META.items():
        if meta["title_bn"] == title_bn:
            return book_id
    return None


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


def duplicate_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(re.findall(r'"(jibanananda-[^"]+)"', path.read_text(encoding="utf-8")))


def load_page_corpus_context(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    rows = read_jsonl(path)
    exact_pages: set[tuple[str, int]] = set()
    canonical_ranges: dict[str, dict[str, int]] = {}
    for row in rows:
        book_id = row.get("book_id")
        printed_page = row.get("printed_page_fixed")
        if not isinstance(book_id, str) or not isinstance(printed_page, int):
            continue
        exact_pages.add((book_id, printed_page))
        canonical = canonical_book_id(book_id)
        if canonical is None:
            continue
        existing = canonical_ranges.get(canonical)
        if existing is None:
            canonical_ranges[canonical] = {"min": printed_page, "max": printed_page}
            continue
        existing["min"] = min(existing["min"], printed_page)
        existing["max"] = max(existing["max"], printed_page)
    return {"exact_pages": exact_pages, "canonical_ranges": canonical_ranges}


def candidate_pages_exist(row: dict[str, Any], page_context: dict[str, Any] | None) -> bool:
    if page_context is None:
        return True
    book_id = row.get("candidate_book_id")
    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(book_id, str) or not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    exact_pages: set[tuple[str, int]] = page_context["exact_pages"]
    return all((book_id, page_number) in exact_pages for page_number in range(start, end + 1))


def primary_source_for_title(poem: dict[str, Any], title_bn: str) -> dict[str, Any] | None:
    for source in poem.get("book_sources") or []:
        if (
            source.get("role") == "primary"
            and source.get("title_bn") == title_bn
            and source.get("page_basis") == "printed_page"
            and isinstance(source.get("page_start"), int)
            and isinstance(source.get("page_end"), int)
        ):
            return source
    return None


def source_pages_outside_corpus(
    poem: dict[str, Any],
    source_title_bn: str | None,
    page_context: dict[str, Any] | None,
) -> bool:
    if page_context is None:
        return False
    source_book = source_title_book_id(source_title_bn)
    if source_book is None:
        return False
    source = primary_source_for_title(poem, str(source_title_bn))
    if source is None:
        return False
    ranges: dict[str, dict[str, int]] = page_context["canonical_ranges"]
    corpus_range = ranges.get(source_book)
    if corpus_range is None:
        return False
    start = int(source["page_start"])
    end = int(source["page_end"])
    return not any(corpus_range["min"] <= page_number <= corpus_range["max"] for page_number in range(start, end + 1))


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


def has_existing_book_source(poem: dict[str, Any], title_bn: str) -> bool:
    return any(
        source.get("role") == "primary"
        and source.get("title_bn") == title_bn
        and isinstance(source.get("page_start"), int)
        and isinstance(source.get("page_end"), int)
        for source in poem.get("book_sources") or []
    )


def existing_book_source(poem: dict[str, Any], title_bn: str) -> dict[str, Any] | None:
    for source in poem.get("book_sources") or []:
        if (
            source.get("role") == "primary"
            and source.get("title_bn") == title_bn
            and isinstance(source.get("page_start"), int)
            and isinstance(source.get("page_end"), int)
        ):
            return source
    return None


def has_existing_page_range_conflict(row: dict[str, Any], poem: dict[str, Any], title_bn: str) -> bool:
    source = existing_book_source(poem, title_bn)
    if source is None:
        return False
    return (
        source["page_start"] != row.get("printed_page_start")
        or source["page_end"] != row.get("printed_page_end")
    )


def is_known_collection_review_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Allow a narrower review gate for poems already assigned to this book.

    The global acceptance gate has to work for unknown poems across all books.
    If the poem already has a matching source edition, a lower score can still
    be useful when the span has printed pages and multiple line anchors.
    """

    if row.get("status") != "needs_manual_review":
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False
    if not has_span_anchor_evidence(row):
        return False
    if "page_sequence_present" not in set(row.get("evidence") or []):
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 4:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 1:
        return False
    if not (
        {"title_match", "first_line_match", "last_line_match", "high_body_coverage"}
        & set(row.get("evidence") or [])
    ):
        return False

    runner_up_gap = row.get("runner_up_gap")
    return runner_up_gap is None or float(runner_up_gap) >= 4


def is_known_collection_ambiguous_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote dense same-book spans where adjacent pages tie in scoring."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False
    if not has_span_anchor_evidence(row):
        return False

    evidence = set(row.get("evidence") or [])
    if "page_sequence_present" not in evidence:
        return False
    if not ({"title_match", "first_line_match", "last_line_match", "high_body_coverage"} & evidence):
        return False
    if float(row.get("score") or 0) < 25:
        return False
    if int(row.get("span_line_match_count") or 0) < 10:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 5:
        return False

    page_span = int(row["printed_page_end"]) - int(row["printed_page_start"]) + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_known_collection_exact_rich_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote same-book spans with many exact line anchors despite page ties."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False

    evidence = set(row.get("evidence") or [])
    if "page_sequence_present" not in evidence:
        return False
    if not ({"title_match", "first_line_match", "last_line_match", "high_body_coverage"} & evidence):
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 12:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 8:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_known_collection_line_rich_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote same-book spans with multiple exact anchors and printed pages."""

    if row.get("status") not in {"ambiguous", "needs_manual_review"}:
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if not ({"title_match", "first_line_match", "last_line_match", "high_body_coverage", "page_sequence_present"} & evidence):
        return False
    if int(row.get("span_line_match_count") or 0) < 3:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 3:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_known_collection_fuzzy_rich_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote same-book spans with dense fuzzy line coverage and some exact anchors."""

    if row.get("status") not in {"ambiguous", "needs_manual_review"}:
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if not {"title_match", "high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if float(row.get("score") or 0) < 24:
        return False
    if int(row.get("span_line_match_count") or 0) < 18:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 2:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_known_collection_continuation_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote same-book spans where an anchored title page has continuation pages."""

    if row.get("status") != "needs_manual_review":
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if not {"title_match", "body_token_overlap"} <= evidence:
        return False
    if float(row.get("score") or 0) < 22:
        return False
    if int(row.get("span_line_match_count") or 0) < 10:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 4:
        return False
    if int(row.get("span_anchor_count") or 0) < 1:
        return False

    runner_up_gap = row.get("runner_up_gap")
    return runner_up_gap is None or float(runner_up_gap) >= 4


def is_known_collection_long_ambiguous_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote long same-book spans where adjacent page windows tie."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if not {"body_token_overlap", "page_sequence_present"} <= evidence:
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 15:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 2:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_known_collection_single_page_body_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote reviewed one-page same-book spans with high body coverage."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != meta["title_bn"]:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or start != end:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) != 1:
        return False

    evidence = set(row.get("evidence") or [])
    if not {"body_token_overlap", "high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if float(row.get("score") or 0) < 25:
        return False
    if int(row.get("span_line_match_count") or 0) < 7:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 1:
        return False

    return True


def is_unknown_collection_exact_rich_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote unknown-collection spans only when exact line evidence is dense."""

    if row.get("status") not in {"ambiguous", "needs_manual_review"}:
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if "page_sequence_present" not in evidence:
        return False
    if not ({"title_match", "first_line_match", "last_line_match", "high_body_coverage"} & evidence):
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 15:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 10:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_unknown_collection_exact_anchor_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote unknown-collection spans where exact anchors cover the span."""

    if row.get("status") not in {"ambiguous", "needs_manual_review"}:
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if "page_sequence_present" not in evidence:
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 20:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 20:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= page_span


def is_unknown_collection_single_page_body_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote high-coverage one-page unknown poems with line anchors.

    This is intentionally narrower than the general unknown gates. It is aimed
    at titleless one-page poems where the body spans a single printed page and
    the page sequence plus line anchors are the strongest deterministic signal.
    """

    if row.get("status") != "needs_manual_review":
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False
    if row.get("candidate_book_id") != "rupasi-bangla":
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or start != end:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) != 1:
        return False

    evidence = set(row.get("evidence") or [])
    if not {"high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 3:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 1:
        return False

    runner_up_gap = row.get("runner_up_gap")
    return runner_up_gap is None or float(runner_up_gap) >= 4


def is_unknown_collection_ambiguous_line_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Promote ambiguous unknown spans with title/high-body evidence and anchors."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if "body_token_overlap" not in evidence:
        return False
    if not ({"title_match", "high_body_coverage"} & evidence):
        return False
    if float(row.get("score") or 0) < 19:
        return False
    if int(row.get("span_line_match_count") or 0) < 7:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 2:
        return False

    page_span = end - start + 1
    if int(row.get("span_anchor_count") or 0) < min(page_span, 2):
        return False

    runner_up_gap = row.get("runner_up_gap")
    return runner_up_gap is not None and float(runner_up_gap) >= 2


def is_unknown_collection_continuation_body_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Classify unknown multi-page spans with body coverage and a printed page sequence."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) < 1:
        return False

    evidence = set(row.get("evidence") or [])
    if not {"body_token_overlap", "high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 8:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 1:
        return False

    return True


def is_unknown_collection_fuzzy_single_page_body_candidate(
    row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]
) -> bool:
    """Classify reviewed one-page Rupasi Bangla spans with fuzzy body coverage."""

    if row.get("status") != "needs_manual_review":
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False
    if row.get("candidate_book_id") != "rupasi-bangla":
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or start != end:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) != 1:
        return False

    evidence = set(row.get("evidence") or [])
    if not {"body_token_overlap", "high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if float(row.get("score") or 0) < 17:
        return False
    if int(row.get("span_line_match_count") or 0) < 4:
        return False

    return True


def is_unknown_collection_single_page_ambiguous_body_candidate(
    row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]
) -> bool:
    """Classify ambiguous one-page Rupasi Bangla spans with title and body coverage."""

    if row.get("status") != "ambiguous":
        return False
    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False
    if row.get("candidate_book_id") != "rupasi-bangla":
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or start != end:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) != 1:
        return False

    evidence = set(row.get("evidence") or [])
    if not {"body_token_overlap", "high_body_coverage", "page_sequence_present", "title_match"} <= evidence:
        return False
    if float(row.get("score") or 0) < 25:
        return False
    if int(row.get("span_line_match_count") or 0) < 4:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 1:
        return False

    return True


def is_conflict_exact_rich_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Override stale known editions only with dense exact-line evidence."""

    current_edition = poem.get("source_edition")
    if current_edition in {UNKNOWN_COLLECTION, meta["title_bn"]}:
        return False
    if row.get("status") not in {"accepted_candidate", "ambiguous", "needs_manual_review"}:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if "title_match" not in evidence or "page_sequence_present" not in evidence:
        return False
    if float(row.get("score") or 0) < 27:
        return False
    if int(row.get("span_line_match_count") or 0) < 20:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 10:
        return False

    runner_up_gap = row.get("runner_up_gap")
    if runner_up_gap is not None and float(runner_up_gap) < 3:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_conflict_accepted_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Override stale known editions only for accepted high-coverage spans.

    This is narrower than the general conflict override. It requires the
    deterministic span proposer to have accepted the row, and it keeps title or
    opening-line evidence, exact line anchors, page sequence evidence, and a
    clear runner-up gap in the same gate.
    """

    current_edition = poem.get("source_edition")
    if current_edition in {UNKNOWN_COLLECTION, meta["title_bn"]}:
        return False
    if row.get("status") != "accepted_candidate":
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if not {"high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if not ({"title_match", "first_line_match"} & evidence):
        return False
    if float(row.get("score") or 0) < 25:
        return False
    if int(row.get("span_line_match_count") or 0) < 9:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 3:
        return False

    runner_up_gap = row.get("runner_up_gap")
    if runner_up_gap is not None and float(runner_up_gap) < 4:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_outside_range_conflict_candidate(
    row: dict[str, Any],
    poem: dict[str, Any],
    meta: dict[str, Any],
    page_context: dict[str, Any] | None,
) -> bool:
    """Override stale known editions when their current pages are impossible.

    This gate is for records whose existing source citation points outside the
    repaired page range for that source book. It still requires an accepted
    line-anchor candidate whose printed pages exist in the current corpus.
    """

    current_edition = poem.get("source_edition")
    if current_edition in {UNKNOWN_COLLECTION, meta["title_bn"]}:
        return False
    if not source_pages_outside_corpus(poem, str(current_edition or ""), page_context):
        return False
    if row.get("status") != "accepted_candidate":
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if not candidate_pages_exist(row, page_context):
        return False

    evidence = set(row.get("evidence") or [])
    if not {"high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if not ({"title_match", "first_line_match", "last_line_match"} & evidence):
        return False
    if float(row.get("score") or 0) < 29:
        return False
    if int(row.get("span_line_match_count") or 0) < 7:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 2:
        return False

    runner_up_gap = row.get("runner_up_gap")
    if runner_up_gap is not None and float(runner_up_gap) < 8:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_outside_range_alias_repair_candidate(
    row: dict[str, Any],
    poem: dict[str, Any],
    meta: dict[str, Any],
    page_context: dict[str, Any] | None,
) -> bool:
    """Replace a same-book page range only when the current range is impossible."""

    if poem.get("source_edition") != meta["title_bn"]:
        return False
    if row.get("candidate_book_id") not in BOOK_ALIASES:
        return False
    if not source_pages_outside_corpus(poem, meta["title_bn"], page_context):
        return False
    if row.get("status") != "accepted_candidate":
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if not candidate_pages_exist(row, page_context):
        return False

    evidence = set(row.get("evidence") or [])
    if not {"high_body_coverage", "page_sequence_present"} <= evidence:
        return False
    if not ({"title_match", "first_line_match", "last_line_match"} & evidence):
        return False
    if float(row.get("score") or 0) < 25:
        return False
    if int(row.get("span_line_match_count") or 0) < 5:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 3:
        return False

    runner_up_gap = row.get("runner_up_gap")
    if runner_up_gap is not None and float(runner_up_gap) < 8:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def has_embedded_collection_marker(poem: dict[str, Any], title_bn: str) -> bool:
    marker = f"#{title_bn}"
    return any(line.strip() == marker for line in (poem.get("body_bn") or "").splitlines())


def collection_reference_patterns(title_bn: str) -> list[re.Pattern[str]]:
    escaped_title = re.escape(title_bn)
    return [
        re.compile(rf"\s*[\(\[]\s*{escaped_title}\s+কাব্যগ্রন্থ\s*[\)\]]\s*$"),
        re.compile(rf"\s*(?:গ্রন্থ|কাব্যগ্রন্থ)\s*[:：-]\s*{escaped_title}\s*$"),
    ]


def has_embedded_collection_reference(poem: dict[str, Any], title_bn: str) -> bool:
    body = poem.get("body_bn") or ""
    patterns = collection_reference_patterns(title_bn)
    return any(any(pattern.search(line.strip()) for pattern in patterns) for line in body.splitlines())


def is_conflict_embedded_collection_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Override stale editions when the stored body names the candidate book."""

    current_edition = poem.get("source_edition")
    if current_edition in {UNKNOWN_COLLECTION, meta["title_bn"]}:
        return False
    if not has_embedded_collection_marker(poem, meta["title_bn"]):
        return False
    if row.get("status") not in {"accepted_candidate", "ambiguous", "needs_manual_review"}:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False
    if int(row.get("span_anchor_count") or 0) <= 0:
        return False
    if int(row.get("span_line_match_count") or 0) < 4:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 4:
        return False

    return float(row.get("score") or 0) >= 17


def is_unknown_embedded_collection_candidate(row: dict[str, Any], poem: dict[str, Any], meta: dict[str, Any]) -> bool:
    """Classify unknown poems when the stored body carries a source-book marker."""

    if poem.get("source_edition") != UNKNOWN_COLLECTION:
        return False
    if not has_embedded_collection_reference(poem, meta["title_bn"]):
        return False
    if row.get("status") not in {"accepted_candidate", "ambiguous", "needs_manual_review"}:
        return False

    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if row.get("span_basis") != "line_anchor_cluster":
        return False

    evidence = set(row.get("evidence") or [])
    if "page_sequence_present" not in evidence:
        return False
    if int(row.get("span_line_match_count") or 0) < 8:
        return False
    if int(row.get("span_exact_line_match_count") or 0) < 4:
        return False

    page_span = end - start + 1
    return int(row.get("span_anchor_count") or 0) >= min(page_span, 2)


def is_eligible(
    row: dict[str, Any],
    poem: dict[str, Any],
    allow_legacy_candidates: bool,
    allow_known_review_candidates: bool,
    allow_known_ambiguous_candidates: bool,
    allow_known_exact_rich_candidates: bool,
    allow_known_line_rich_candidates: bool,
    allow_known_fuzzy_rich_candidates: bool,
    allow_known_continuation_candidates: bool,
    allow_known_long_ambiguous_candidates: bool,
    allow_known_single_page_body_candidates: bool,
    allow_unknown_exact_rich_candidates: bool,
    allow_unknown_exact_anchor_candidates: bool,
    allow_unknown_single_page_body_candidates: bool,
    allow_unknown_ambiguous_line_candidates: bool,
    allow_unknown_continuation_body_candidates: bool,
    allow_unknown_fuzzy_single_page_body_candidates: bool,
    allow_unknown_single_page_ambiguous_body_candidates: bool,
    allow_unknown_embedded_candidates: bool,
    allow_conflict_exact_rich_candidates: bool,
    allow_conflict_accepted_candidates: bool,
    allow_conflict_embedded_candidates: bool,
    allow_outside_range_conflict_candidates: bool,
    allow_outside_range_alias_repairs: bool,
    page_context: dict[str, Any] | None,
) -> tuple[bool, str]:
    if poem.get("poet_id") != "jibanananda-das":
        return False, "not_jibanananda"
    if not isinstance(row.get("printed_page_start"), int) or not isinstance(row.get("printed_page_end"), int):
        return False, "missing_printed_page"

    meta = BOOK_META.get(row.get("candidate_book_id"))
    if not meta:
        return False, "unknown_candidate_book"
    if page_context is not None and not candidate_pages_exist(row, page_context):
        return False, "candidate_pages_not_in_corpus"

    if allow_conflict_exact_rich_candidates and is_conflict_exact_rich_candidate(row, poem, meta):
        return True, "eligible_conflict_exact_rich"
    if allow_conflict_accepted_candidates and is_conflict_accepted_candidate(row, poem, meta):
        return True, "eligible_conflict_accepted"
    if allow_conflict_embedded_candidates and is_conflict_embedded_collection_candidate(row, poem, meta):
        return True, "eligible_conflict_embedded_collection"
    if allow_outside_range_conflict_candidates and is_outside_range_conflict_candidate(row, poem, meta, page_context):
        return True, "eligible_outside_range_conflict"
    if allow_outside_range_alias_repairs and is_outside_range_alias_repair_candidate(row, poem, meta, page_context):
        return True, "eligible_outside_range_alias_repair"
    if row.get("candidate_book_id") in BOOK_ALIASES and has_existing_book_source(poem, meta["title_bn"]):
        return False, "alias_existing_source"

    if row.get("status") != "accepted_candidate":
        if allow_known_review_candidates and is_known_collection_review_candidate(row, poem, meta):
            return True, "eligible_known_review"
        if allow_known_ambiguous_candidates and is_known_collection_ambiguous_candidate(row, poem, meta):
            return True, "eligible_known_ambiguous"
        if allow_known_exact_rich_candidates and is_known_collection_exact_rich_candidate(row, poem, meta):
            return True, "eligible_known_exact_rich"
        if allow_known_line_rich_candidates and is_known_collection_line_rich_candidate(row, poem, meta):
            return True, "eligible_known_line_rich"
        if allow_known_fuzzy_rich_candidates and is_known_collection_fuzzy_rich_candidate(row, poem, meta):
            return True, "eligible_known_fuzzy_rich"
        if allow_known_continuation_candidates and is_known_collection_continuation_candidate(row, poem, meta):
            return True, "eligible_known_continuation"
        if allow_known_long_ambiguous_candidates and is_known_collection_long_ambiguous_candidate(row, poem, meta):
            return True, "eligible_known_long_ambiguous"
        if allow_known_single_page_body_candidates and is_known_collection_single_page_body_candidate(row, poem, meta):
            return True, "eligible_known_single_page_body"
        if allow_unknown_exact_rich_candidates and is_unknown_collection_exact_rich_candidate(row, poem, meta):
            return True, "eligible_unknown_exact_rich"
        if allow_unknown_exact_anchor_candidates and is_unknown_collection_exact_anchor_candidate(row, poem, meta):
            return True, "eligible_unknown_exact_anchor"
        if allow_unknown_single_page_body_candidates and is_unknown_collection_single_page_body_candidate(row, poem, meta):
            return True, "eligible_unknown_single_page_body"
        if allow_unknown_ambiguous_line_candidates and is_unknown_collection_ambiguous_line_candidate(row, poem, meta):
            return True, "eligible_unknown_ambiguous_line"
        if allow_unknown_continuation_body_candidates and is_unknown_collection_continuation_body_candidate(row, poem, meta):
            return True, "eligible_unknown_continuation_body"
        if allow_unknown_fuzzy_single_page_body_candidates and is_unknown_collection_fuzzy_single_page_body_candidate(
            row, poem, meta
        ):
            return True, "eligible_unknown_fuzzy_single_page_body"
        if (
            allow_unknown_single_page_ambiguous_body_candidates
            and is_unknown_collection_single_page_ambiguous_body_candidate(row, poem, meta)
        ):
            return True, "eligible_unknown_single_page_ambiguous_body"
        if allow_unknown_embedded_candidates and is_unknown_embedded_collection_candidate(row, poem, meta):
            return True, "eligible_unknown_embedded_collection"
        return False, "not_accepted"
    if not allow_legacy_candidates and not has_span_anchor_evidence(row):
        return False, "missing_span_anchor_evidence"

    current_edition = poem.get("source_edition")
    if current_edition != UNKNOWN_COLLECTION and current_edition != meta["title_bn"]:
        return False, "known_collection_conflict"

    return True, "eligible"


def remove_embedded_collection_marker(poem: dict[str, Any], title_bn: str) -> None:
    marker = f"#{title_bn}"
    patterns = collection_reference_patterns(title_bn)
    lines = (poem.get("body_bn") or "").splitlines()
    filtered = []
    for line in lines:
        if line.strip() == marker:
            continue
        cleaned = line.rstrip()
        for pattern in patterns:
            cleaned = pattern.sub("", cleaned).rstrip()
        filtered.append(cleaned)
    if len(filtered) != len(lines):
        poem["body_bn"] = "\n".join(filtered)
        return
    next_body = "\n".join(filtered)
    if next_body != poem.get("body_bn"):
        poem["body_bn"] = next_body


def apply_metadata(row: dict[str, Any], poem: dict[str, Any], force_collection_update: bool = False) -> bool:
    meta = BOOK_META[row["candidate_book_id"]]
    before = json.dumps(poem, ensure_ascii=False, sort_keys=True)

    if poem.get("source_edition") == UNKNOWN_COLLECTION or force_collection_update:
        poem["source_edition"] = meta["title_bn"]
        poem["source_year"] = meta["publication_year"]
        poem["phase_id"] = meta["phase_id"]
        remove_embedded_collection_marker(poem, meta["title_bn"])

    source = book_source(row, meta)
    existing_sources = poem.get("book_sources") or []
    if force_collection_update or poem.get("source_edition") == source["title_bn"]:
        next_sources = [item for item in existing_sources if item.get("role") != source["role"]]
    else:
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
    parser.add_argument("--duplicates-source", default=DEFAULT_DUPLICATES_SOURCE)
    parser.add_argument(
        "--page-corpus",
        default=None,
        help="Optional repaired page corpus used to verify candidate printed pages and outside-range repairs.",
    )
    parser.add_argument(
        "--include-duplicates",
        action="store_true",
        help="Also apply to duplicate-hidden poem imports listed in the duplicates source.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-legacy-candidates",
        action="store_true",
        help="Allow accepted candidates that do not include deterministic span-anchor evidence.",
    )
    parser.add_argument(
        "--allow-known-review-candidates",
        action="store_true",
        help="Apply printed-page citations for review candidates whose candidate book already matches the poem source edition.",
    )
    parser.add_argument(
        "--allow-known-ambiguous-candidates",
        action="store_true",
        help="Apply dense same-book ambiguous spans where adjacent pages tie in scoring.",
    )
    parser.add_argument(
        "--allow-known-exact-rich-candidates",
        action="store_true",
        help="Apply same-book ambiguous spans with many exact line anchors despite page-score ties.",
    )
    parser.add_argument(
        "--allow-known-line-rich-candidates",
        action="store_true",
        help="Apply same-book review/ambiguous spans with multiple exact line anchors.",
    )
    parser.add_argument(
        "--allow-known-fuzzy-rich-candidates",
        action="store_true",
        help="Apply same-book review/ambiguous spans with dense fuzzy line coverage and some exact anchors.",
    )
    parser.add_argument(
        "--allow-known-continuation-candidates",
        action="store_true",
        help="Apply same-book review spans where a title/line-anchor page has continuation pages.",
    )
    parser.add_argument(
        "--allow-known-long-ambiguous-candidates",
        action="store_true",
        help="Apply long same-book ambiguous spans with dense line anchors and printed page sequence evidence.",
    )
    parser.add_argument(
        "--allow-known-single-page-body-candidates",
        action="store_true",
        help="Apply same-book one-page ambiguous spans with reviewed body coverage and printed page sequence evidence.",
    )
    parser.add_argument(
        "--allow-unknown-exact-rich-candidates",
        action="store_true",
        help="Classify unknown-collection review/ambiguous spans with dense exact line anchors.",
    )
    parser.add_argument(
        "--allow-unknown-exact-anchor-candidates",
        action="store_true",
        help="Classify unknown-collection spans whose exact line anchors cover the full printed span.",
    )
    parser.add_argument(
        "--allow-unknown-single-page-body-candidates",
        action="store_true",
        help="Classify unknown one-page Rupasi Bangla spans with high body coverage, page sequence evidence, and line anchors.",
    )
    parser.add_argument(
        "--allow-unknown-ambiguous-line-candidates",
        action="store_true",
        help="Classify unknown ambiguous spans with title/high-body evidence, line anchors, and printed pages.",
    )
    parser.add_argument(
        "--allow-unknown-continuation-body-candidates",
        action="store_true",
        help="Classify unknown multi-page ambiguous spans with body coverage and printed page sequence evidence.",
    )
    parser.add_argument(
        "--allow-unknown-fuzzy-single-page-body-candidates",
        action="store_true",
        help="Classify unknown one-page Rupasi Bangla spans with fuzzy body coverage and printed page sequence evidence.",
    )
    parser.add_argument(
        "--allow-unknown-single-page-ambiguous-body-candidates",
        action="store_true",
        help="Classify unknown one-page Rupasi Bangla ambiguous spans with title/body coverage and printed page sequence evidence.",
    )
    parser.add_argument(
        "--allow-unknown-embedded-candidates",
        action="store_true",
        help="Classify unknown-collection spans when the poem body embeds the candidate collection as a source marker.",
    )
    parser.add_argument(
        "--allow-conflict-exact-rich-candidates",
        action="store_true",
        help="Override stale known collection assignments with dense exact line-anchor evidence.",
    )
    parser.add_argument(
        "--allow-conflict-accepted-candidates",
        action="store_true",
        help="Override stale known collection assignments for accepted high-coverage all-books candidates.",
    )
    parser.add_argument(
        "--allow-conflict-embedded-candidates",
        action="store_true",
        help="Override stale known collection assignments when the poem body embeds the candidate collection marker.",
    )
    parser.add_argument(
        "--allow-outside-range-conflict-candidates",
        action="store_true",
        help="Override stale known collection assignments only when the existing citation is outside the source corpus range.",
    )
    parser.add_argument(
        "--allow-outside-range-alias-repairs",
        action="store_true",
        help="Replace same-book page ranges from alias/copy evidence only when the existing range is outside the corpus range.",
    )
    parser.add_argument(
        "--allow-existing-page-overwrite",
        action="store_true",
        help="Allow a candidate to replace an existing printed primary page range for the same book.",
    )
    args = parser.parse_args()

    poems_dir = Path(args.poems_dir)
    rows = read_jsonl(Path(args.candidates))
    duplicates = duplicate_ids(Path(args.duplicates_source))
    page_context = load_page_corpus_context(Path(args.page_corpus)) if args.page_corpus else None
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
        if not args.include_duplicates and poem.get("id") in duplicates:
            summary["duplicate_hidden"] = summary.get("duplicate_hidden", 0) + 1
            continue
        eligible, reason = is_eligible(
            row,
            poem,
            args.allow_legacy_candidates,
            args.allow_known_review_candidates,
            args.allow_known_ambiguous_candidates,
            args.allow_known_exact_rich_candidates,
            args.allow_known_line_rich_candidates,
            args.allow_known_fuzzy_rich_candidates,
            args.allow_known_continuation_candidates,
            args.allow_known_long_ambiguous_candidates,
            args.allow_known_single_page_body_candidates,
            args.allow_unknown_exact_rich_candidates,
            args.allow_unknown_exact_anchor_candidates,
            args.allow_unknown_single_page_body_candidates,
            args.allow_unknown_ambiguous_line_candidates,
            args.allow_unknown_continuation_body_candidates,
            args.allow_unknown_fuzzy_single_page_body_candidates,
            args.allow_unknown_single_page_ambiguous_body_candidates,
            args.allow_unknown_embedded_candidates,
            args.allow_conflict_exact_rich_candidates,
            args.allow_conflict_accepted_candidates,
            args.allow_conflict_embedded_candidates,
            args.allow_outside_range_conflict_candidates,
            args.allow_outside_range_alias_repairs,
            page_context,
        )
        if not eligible:
            summary[reason] = summary.get(reason, 0) + 1
            continue

        force_collection_update = reason in {
            "eligible_conflict_exact_rich",
            "eligible_conflict_accepted",
            "eligible_conflict_embedded_collection",
            "eligible_outside_range_conflict",
        }
        force_page_overwrite = reason in {
            "eligible_outside_range_alias_repair",
        }
        meta = BOOK_META[row["candidate_book_id"]]
        if (
            not args.allow_existing_page_overwrite
            and not force_collection_update
            and not force_page_overwrite
            and has_existing_page_range_conflict(row, poem, meta["title_bn"])
        ):
            summary["existing_page_range_conflict"] = summary.get("existing_page_range_conflict", 0) + 1
            continue

        summary[reason] = summary.get(reason, 0) + 1
        if apply_metadata(row, poem, force_collection_update):
            changed.append(filename)
            if not args.dry_run:
                write_json(path, poem)

    print(json.dumps({"summary": summary, "changed": changed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
