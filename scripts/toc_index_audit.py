#!/usr/bin/env python3
"""Review poem title/page matches from scanned table-of-contents pages.

This script reads OCR sidecars only and never mutates poem JSON. It is meant as
another deterministic source of printed-page evidence for metadata gaps where
body OCR matching is too noisy.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import apply_poem_metadata as apply_meta
import propose_poem_spans as spans


BANGLA_TO_ASCII = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
PAGE_NUMBER_RE = re.compile(r"(?<![০-৯0-9])([০-৯0-9]{1,3})(?![০-৯0-9])")
UNKNOWN_COLLECTION = "সংকলন অজানা"
DEFAULT_REVIEW_EXCLUSIONS = "src/data/metadata-review-exclusions.json"
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def load_public_gap_poems(poems_dir: Path, duplicates: set[str]) -> list[tuple[str, dict[str, Any]]]:
    poems = []
    for path in sorted(glob.glob(str(poems_dir / "*.json"))):
        poem = read_json(Path(path))
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if poem.get("id") in duplicates:
            continue
        if has_primary_printed_pages(poem):
            continue
        poems.append((Path(path).name, poem))
    return poems


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


def duplicate_title_context(
    poem: dict[str, Any],
    title_groups: dict[str, list[tuple[str, dict[str, Any]]]],
) -> list[dict[str, Any]]:
    key = compact_title(str(poem.get("title_bn") or ""))
    matches = []
    for filename, other in title_groups.get(key, []):
        if other.get("id") == poem.get("id"):
            continue
        primary_sources = [
            source
            for source in other.get("book_sources") or []
            if source.get("role") == "primary" and source.get("page_basis") == "printed_page"
        ]
        matches.append(
            {
                "filename": filename,
                "poem_id": other.get("id"),
                "source_edition": other.get("source_edition"),
                "source_year": other.get("source_year"),
                "primary_printed_pages": [
                    {
                        "title_bn": source.get("title_bn"),
                        "page_start": source.get("page_start"),
                        "page_end": source.get("page_end"),
                    }
                    for source in primary_sources
                ],
            }
        )
    return matches


def physical_book_id(row: dict[str, Any]) -> str:
    return str(row.get("physical_book_id") or row.get("book_id") or "")


def bn_int(raw: str) -> int | None:
    value = raw.translate(BANGLA_TO_ASCII)
    if not value.isdigit():
        return None
    parsed = int(value)
    if 1 <= parsed <= 500:
        return parsed
    return None


def bengali_char_count(text: str) -> int:
    return len(re.findall(r"[\u0980-\u09FF]", text or ""))


def clean_toc_title(text: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", text or "")
    text = re.sub(r"[\"'‘’“”`*_+=£€|/\\[\]{}<>:;.,।!?—–-]+", " ", text)
    text = re.sub(r"^[\s০-৯0-9]+|[\s০-৯0-9]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalized_title(text: str) -> str:
    return spans.normalize(clean_toc_title(text))


def compact_title(text: str) -> str:
    return re.sub(r"\s+", "", normalized_title(text))


def title_tokens(text: str) -> set[str]:
    return {token for token in normalized_title(text).split() if len(token) >= 2 or token.isdigit()}


def title_similarity(expected: str, candidate: str) -> dict[str, Any]:
    expected_compact = compact_title(expected)
    candidate_compact = compact_title(candidate)
    if not expected_compact or not candidate_compact:
        return {"score": 0.0, "ratio": 0.0, "token_f1": 0.0, "containment": False}

    ratio = SequenceMatcher(None, expected_compact, candidate_compact).ratio()
    expected_tokens = title_tokens(expected)
    candidate_tokens = title_tokens(candidate)
    if expected_tokens and candidate_tokens:
        overlap = len(expected_tokens & candidate_tokens)
        precision = overlap / len(candidate_tokens)
        recall = overlap / len(expected_tokens)
        token_f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    else:
        token_f1 = 0.0

    containment = False
    containment_score = 0.0
    shorter = min(len(expected_compact), len(candidate_compact))
    if shorter >= 5 and (expected_compact in candidate_compact or candidate_compact in expected_compact):
        containment = True
        length_ratio = shorter / max(len(expected_compact), len(candidate_compact))
        if length_ratio >= 0.72:
            containment_score = 0.90 + (0.10 * length_ratio)

    return {
        "score": round(max(ratio, token_f1, containment_score), 4),
        "ratio": round(ratio, 4),
        "token_f1": round(token_f1, 4),
        "containment": containment,
    }


def toc_source_texts(row: dict[str, Any]) -> list[tuple[str, str]]:
    texts = []
    for source_name in ("raw_ocr", "raw_pdftotext"):
        text = row.get(source_name) or ""
        if text.strip():
            texts.append((source_name, text))
    for index, profile in enumerate(row.get("ocr_profiles") or []):
        text = profile.get("text") or ""
        if text.strip():
            texts.append((f"profile:{profile.get('profile') or index}", text))
    return texts


def looks_like_toc(row: dict[str, Any]) -> bool:
    text = "\n".join(text for _, text in toc_source_texts(row))
    normalized = spans.normalize(text)
    return row.get("page_type") == "front_matter" and bool(re.search(r"সূচ|সুচ", normalized))


def likely_toc_continuation(row: dict[str, Any]) -> bool:
    if row.get("page_type") != "front_matter":
        return False
    text = "\n".join(text for _, text in toc_source_texts(row))
    if len(PAGE_NUMBER_RE.findall(text)) < 8:
        return False
    return bengali_char_count(text) >= 80


def toc_page_record_ids(rows: list[dict[str, Any]]) -> set[str]:
    by_physical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_physical[physical_book_id(row)].append(row)

    record_ids = set()
    for physical_id, group in by_physical.items():
        active = False
        previous_scan = None
        for row in sorted(group, key=lambda item: int(item.get("scan_page") or 0)):
            scan_page = row.get("scan_page")
            if looks_like_toc(row):
                active = True
            elif active and previous_scan is not None and isinstance(scan_page, int) and scan_page > previous_scan + 1:
                active = False
            elif active and not likely_toc_continuation(row):
                active = False

            if active and (looks_like_toc(row) or likely_toc_continuation(row)):
                record_id = row.get("record_id")
                if record_id:
                    record_ids.add(str(record_id))
            previous_scan = scan_page if isinstance(scan_page, int) else previous_scan
    return record_ids


def parse_toc_line(line: str) -> list[dict[str, Any]]:
    matches = list(PAGE_NUMBER_RE.finditer(line))
    entries = []
    previous_end = 0
    for match in matches:
        page = bn_int(match.group(1))
        title = clean_toc_title(line[previous_end : match.start()])
        previous_end = match.end()
        if page is None:
            continue
        if bengali_char_count(title) < 2:
            continue
        if normalized_title(title) in {"সূচিপত্র", "সুচিপত্র"}:
            continue
        entries.append({"title": title, "page": page})
    return entries


def page_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    lookup: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        page = row.get("printed_page_fixed")
        if isinstance(page, int):
            lookup[(physical_book_id(row), page)].append(row)
    return lookup


def choose_page_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    def rank(row: dict[str, Any]) -> tuple[int, int, float]:
        return (
            1 if row.get("page_type") in TRUSTED_PAGE_TYPES else 0,
            1 if row.get("printed_page_basis") != "missing" else 0,
            float(row.get("sequence_confidence") or 0),
        )

    return sorted(rows, key=rank, reverse=True)[0]


def extract_toc_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = page_lookup(rows)
    toc_record_ids = toc_page_record_ids(rows)
    entries = []
    seen = set()
    for row in rows:
        if str(row.get("record_id") or "") not in toc_record_ids:
            continue
        physical_id = physical_book_id(row)
        for source_name, text in toc_source_texts(row):
            for line_index, raw_line in enumerate(text.splitlines()):
                for parsed in parse_toc_line(raw_line):
                    page_row = choose_page_row(lookup.get((physical_id, parsed["page"]), []))
                    candidate_book_id = (page_row or row).get("book_id")
                    key = (
                        physical_id,
                        candidate_book_id,
                        parsed["page"],
                        normalized_title(parsed["title"]),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    meta = apply_meta.BOOK_META.get(str(candidate_book_id or ""))
                    entries.append(
                        {
                            "toc_physical_book_id": physical_id,
                            "toc_book_id": row.get("book_id"),
                            "toc_pdf_file": row.get("pdf_file"),
                            "toc_scan_page": row.get("scan_page"),
                            "toc_source": source_name,
                            "toc_line_index": line_index,
                            "toc_line": raw_line.strip(),
                            "entry_title": parsed["title"],
                            "entry_title_normalized": normalized_title(parsed["title"]),
                            "printed_page": parsed["page"],
                            "page_sequence_verified": page_row is not None,
                            "candidate_book_id": candidate_book_id,
                            "candidate_collection_bn": (page_row or row).get("collection_bn"),
                            "candidate_scan_page": (page_row or {}).get("scan_page"),
                            "candidate_pdf_file": (page_row or row).get("pdf_file"),
                            "candidate_source_kind": (page_row or row).get("source_kind"),
                            "canonical_title_bn": (meta or {}).get("title_bn"),
                            "canonical_publication_year": (meta or {}).get("publication_year"),
                            "canonical_phase_id": (meta or {}).get("phase_id"),
                        }
                    )
    return entries


def allowed_book_ids(poem: dict[str, Any], include_logical_aliases: bool) -> set[str] | None:
    edition = poem.get("source_edition")
    if edition == UNKNOWN_COLLECTION:
        return None
    if edition in spans.COLLECTION_TO_BOOK_ID:
        return spans.collection_book_ids(edition, include_logical_aliases)
    return set()


def candidate_status(
    best: dict[str, Any],
    runner_up: dict[str, Any] | None,
    poem: dict[str, Any],
    duplicate_context: list[dict[str, Any]],
) -> str:
    score = float(best.get("title_score") or 0)
    runner_score = float((runner_up or {}).get("title_score") or 0)
    gap = score - runner_score
    has_page = bool(best.get("page_sequence_verified"))
    canonical_title = best.get("canonical_title_bn")
    source_edition = poem.get("source_edition")
    known_conflict = source_edition not in {UNKNOWN_COLLECTION, canonical_title}

    if not has_page:
        return "needs_page_sequence"
    if duplicate_context:
        return "duplicate_title_conflict"
    if known_conflict:
        return "known_source_conflict"
    if score >= 0.985 and gap >= 0.025:
        return "strong_toc_review"
    if score >= 0.93 and gap >= 0.04:
        return "manual_toc_review"
    if score >= 0.86:
        return "weak_toc_review"
    return "weak_toc_review"


def build_matches(
    poems: list[tuple[str, dict[str, Any]]],
    entries: list[dict[str, Any]],
    title_groups: dict[str, list[tuple[str, dict[str, Any]]]],
    include_logical_aliases: bool,
    min_score: float,
    max_candidates: int,
) -> list[dict[str, Any]]:
    rows = []
    for filename, poem in poems:
        allowed = allowed_book_ids(poem, include_logical_aliases)
        scored = []
        for entry in entries:
            candidate_book_id = str(entry.get("candidate_book_id") or "")
            if allowed is not None and candidate_book_id not in allowed:
                continue
            similarity = title_similarity(str(poem.get("title_bn") or ""), str(entry.get("entry_title") or ""))
            if float(similarity["score"]) < min_score:
                continue
            scored.append({**entry, **similarity, "title_score": similarity["score"]})
        scored.sort(
            key=lambda item: (
                bool(item.get("page_sequence_verified")),
                float(item.get("title_score") or 0),
                float(item.get("ratio") or 0),
                -int(item.get("printed_page") or 0),
            ),
            reverse=True,
        )
        if not scored:
            continue
        best = scored[0]
        runner_up = scored[1] if len(scored) > 1 else None
        duplicate_context = duplicate_title_context(poem, title_groups)
        rows.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_edition": poem.get("source_edition"),
                "source_year": poem.get("source_year"),
                "status": candidate_status(best, runner_up, poem, duplicate_context),
                "duplicate_title_context": duplicate_context,
                "best_candidate": best,
                "top_candidates": scored[:max_candidates],
                "runner_up_score": (runner_up or {}).get("title_score"),
            }
        )
    rows.sort(
        key=lambda row: (
            row.get("status") != "strong_toc_review",
            row.get("status") != "manual_toc_review",
            row.get("status") != "weak_toc_review",
            -float((row.get("best_candidate") or {}).get("title_score") or 0),
            row.get("filename") or "",
        )
    )
    return rows


def page_label(candidate: dict[str, Any]) -> str:
    page = candidate.get("printed_page")
    return str(page) if isinstance(page, int) else "পৃষ্ঠা নেই"


def markdown_report(rows: list[dict[str, Any]], summary: dict[str, Any], max_rows: int) -> str:
    lines = [
        "# TOC Index Audit",
        "",
        "Review-only title/page matches from scanned table-of-contents OCR.",
        "",
        f"- TOC entries parsed: {summary['toc_entry_count']}",
        f"- Remaining gap poems checked: {summary['poem_count']}",
        f"- Rows with TOC candidates: {summary['matched_poem_count']}",
        "",
        "| file | title | current source | status | candidate | score | TOC line |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in rows[:max_rows]:
        candidate = row.get("best_candidate") or {}
        candidate_summary = (
            f"{candidate.get('candidate_book_id')}; p.{page_label(candidate)}; "
            f"scan {candidate.get('candidate_scan_page') or '-'}; "
            f"{candidate.get('entry_title')}"
        )
        source = " ".join(str(part) for part in [row.get("source_edition"), row.get("source_year") or ""] if part)
        toc_line = str(candidate.get("toc_line") or "").replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or ""),
                    source,
                    str(row.get("status") or ""),
                    candidate_summary.replace("|", "\\|"),
                    str(candidate.get("title_score") or ""),
                    toc_line,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review TOC title/page matches for Jibanananda metadata gaps.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.layout.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument("--ocr-substitutions", default=None)
    parser.add_argument("--output", default="metadata_reports/toc-index-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/toc-index-audit.current.md")
    parser.add_argument("--min-score", type=float, default=0.82)
    parser.add_argument("--max-candidates", type=int, default=6)
    parser.add_argument("--max-rows", type=int, default=120)
    parser.add_argument("--include-logical-aliases", action="store_true")
    args = parser.parse_args()

    spans.OCR_SUBSTITUTIONS = spans.load_ocr_substitutions(
        Path(args.ocr_substitutions) if args.ocr_substitutions else None
    )

    pages = read_jsonl(Path(args.page_corpus))
    entries = extract_toc_entries(pages)
    duplicates = duplicate_ids(Path(args.duplicates_source))
    all_poems = load_public_jibanananda_poems(Path(args.poems_dir), duplicates)
    title_groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for filename, poem in all_poems:
        title_groups[compact_title(str(poem.get("title_bn") or ""))].append((filename, poem))
    poems = [
        (filename, poem)
        for filename, poem in all_poems
        if not has_primary_printed_pages(poem)
    ]
    rows = build_matches(
        poems,
        entries,
        title_groups,
        include_logical_aliases=args.include_logical_aliases,
        min_score=args.min_score,
        max_candidates=args.max_candidates,
    )
    summary = {
        "toc_entry_count": len(entries),
        "toc_entry_count_by_book": dict(Counter(str(entry.get("candidate_book_id")) for entry in entries).most_common()),
        "poem_count": len(poems),
        "matched_poem_count": len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows).most_common()),
        "min_score": args.min_score,
        "include_logical_aliases": args.include_logical_aliases,
        "note": "Review-only report; it does not mutate poem JSON.",
    }
    payload = {"summary": summary, "matches": rows, "toc_entries": entries}
    write_json(Path(args.output), payload)

    markdown = markdown_report(rows, summary, args.max_rows)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
