#!/usr/bin/env python3
"""Review possible poem composition dates from cited OCR page spans.

This is a diagnostic step only. It reads current poem JSON plus the repaired
page corpus, writes sidecar reports, and never mutates poem data.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import propose_poem_spans as spans


DEFAULT_PAGE_CORPUS = "metadata_reports/page-corpus.full.repaired.layout.jsonl"
DEFAULT_DUPLICATES_SOURCE = "src/lib/content.ts"
TRUSTED_PAGE_TYPES = {
    "normal_poem_page",
    "poem_or_text_page",
    "poem_start_or_short_page",
}
MONTHS = [
    "বৈশাখ",
    "জ্যৈষ্ঠ",
    "জৈষ্ঠ",
    "আষাঢ়",
    "আষাঢ়",
    "শ্রাবণ",
    "ভাদ্র",
    "আশ্বিন",
    "কার্তিক",
    "অগ্রহায়ণ",
    "অগ্রহায়ণ",
    "পৌষ",
    "মাঘ",
    "ফাল্গুন",
    "চৈত্র",
]
LOCATION_WORDS = [
    "কলিকাতা",
    "কলকাতা",
    "বরিশাল",
    "ঢাকা",
    "দার্জিলিং",
    "লাবণ্যপ্রভা",
    "প্রেসিডেন্সি",
]
MONTH_RE = re.compile(r"(?:" + "|".join(re.escape(month) for month in MONTHS) + r")")
DIGIT_RE = re.compile(r"[\u09E6-\u09EF0-9]")
YEAR_RE = re.compile(r"(?:১৩[০-৯]{2}|১৪[০-৯]{2}|18[0-9]{2}|19[0-9]{2})")


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


def load_public_poems(poems_dir: Path, duplicates: set[str]) -> list[tuple[str, dict[str, Any]]]:
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


def source_book_id(poem: dict[str, Any], source: dict[str, Any]) -> str | None:
    for key in (source.get("title_bn"), poem.get("source_edition")):
        if key in spans.COLLECTION_TO_BOOK_ID:
            return spans.COLLECTION_TO_BOOK_ID[str(key)]
    return None


def load_page_index(path: Path, trusted_only: bool) -> dict[tuple[str, int], list[dict[str, Any]]]:
    index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(path):
        if trusted_only and row.get("page_type") not in TRUSTED_PAGE_TYPES:
            continue
        page = row.get("printed_page_fixed")
        book_id = row.get("book_id")
        if isinstance(page, int) and isinstance(book_id, str):
            index[(book_id, page)].append(row)
    return dict(index)


def profile_text(page: dict[str, Any]) -> str:
    return "\n".join(profile.get("text") or "" for profile in page.get("ocr_profiles") or [])


def page_line_sources(page: dict[str, Any]) -> list[dict[str, Any]]:
    lines = []
    for source_name, text in (
        ("ocr", page.get("raw_ocr") or ""),
        ("pdftotext", page.get("raw_pdftotext") or ""),
        ("profiles", profile_text(page)),
    ):
        for line_index, raw in enumerate(text.splitlines()):
            raw = raw.strip()
            normalized = spans.normalize(raw)
            if not normalized:
                continue
            lines.append(
                {
                    "source": source_name,
                    "line_index": line_index,
                    "raw": raw,
                    "normalized": normalized,
                }
            )
    return lines


def normalized_body_lines(body: str, min_chars: int) -> list[dict[str, Any]]:
    lines = []
    for line_index, raw in enumerate((body or "").splitlines()):
        normalized = spans.normalize(raw)
        compact = re.sub(r"\s+", "", normalized)
        if len(compact) < min_chars:
            continue
        lines.append({"line_index": line_index, "raw": raw.strip(), "normalized": normalized})
    return lines


def is_short_signature_line(raw: str) -> bool:
    compact = re.sub(r"\s+", "", raw)
    return 4 <= len(compact) <= 48


def date_strength(raw: str) -> tuple[bool, list[str]]:
    evidence = []
    has_month = bool(MONTH_RE.search(raw))
    has_digit = bool(DIGIT_RE.search(raw))
    has_year = bool(YEAR_RE.search(raw))
    has_location = any(word in raw for word in LOCATION_WORDS)
    if has_month:
        evidence.append("month_word")
    if has_month and has_digit:
        evidence.append("month_with_number")
    if has_year:
        evidence.append("year_pattern")
    if has_location:
        evidence.append("location_word")
    if not is_short_signature_line(raw):
        return False, evidence
    return bool("month_with_number" in evidence or (has_year and has_location)), evidence


def find_body_matches(
    body_lines: list[dict[str, Any]],
    page_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches = []
    for body_line in body_lines:
        text = str(body_line.get("normalized") or "")
        if not text:
            continue
        for page_line in page_lines:
            page_text = str(page_line.get("normalized") or "")
            if text == page_text or text in page_text or page_text in text:
                matches.append(
                    {
                        "body_line_index": int(body_line["line_index"]),
                        "page_line_index": int(page_line["line_index"]),
                        "page_region_source": page_line["source"],
                    }
                )
    return matches


def candidate_search_lines(
    page: dict[str, Any],
    page_lines: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    tail_line_count: int,
    after_match_window: int,
) -> list[tuple[str, dict[str, Any], str]]:
    output: list[tuple[str, dict[str, Any], str]] = []
    if matches:
        last_body_index = max(int(match["body_line_index"]) for match in matches)
        anchors = [match for match in matches if int(match["body_line_index"]) >= last_body_index - 2]
        for match in anchors:
            source = str(match["page_region_source"])
            start = int(match["page_line_index"]) + 1
            end = start + after_match_window
            for line in page_lines:
                if line["source"] == source and start <= int(line["line_index"]) <= end:
                    output.append(("after_last_matched_body_line", line, source))

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in page_lines:
        by_source[str(line["source"])].append(line)
    for source, lines in by_source.items():
        for line in lines[-tail_line_count:]:
            output.append(("tail_of_cited_page", line, source))

    seen = set()
    unique = []
    for basis, line, source in output:
        key = (basis, source, line["line_index"], line["raw"])
        if key in seen:
            continue
        seen.add(key)
        unique.append((basis, line, source))
    return unique


def audit_poem(
    filename: str,
    poem: dict[str, Any],
    page_index: dict[tuple[str, int], list[dict[str, Any]]],
    min_body_line_chars: int,
    tail_line_count: int,
    after_match_window: int,
) -> dict[str, Any]:
    sources = primary_printed_sources(poem)
    if not sources:
        return {
            "filename": filename,
            "poem_id": poem.get("id"),
            "title_bn": poem.get("title_bn"),
            "status": "no_primary_printed_page",
            "candidates": [],
        }

    body_lines = normalized_body_lines(str(poem.get("body_bn") or ""), min_body_line_chars)
    candidates = []
    pages_checked = []
    for source in sources:
        book_id = source_book_id(poem, source)
        if not book_id:
            continue
        page_start = int(source["page_start"])
        page_end = int(source["page_end"])
        for printed_page in range(page_start, page_end + 1):
            for page in page_index.get((book_id, printed_page), []):
                pages_checked.append(
                    {
                        "book_id": book_id,
                        "printed_page": printed_page,
                        "scan_page": page.get("scan_page"),
                    }
                )
                page_lines = page_line_sources(page)
                matches = find_body_matches(body_lines, page_lines)
                for basis, line, source_name in candidate_search_lines(
                    page,
                    page_lines,
                    matches,
                    tail_line_count=tail_line_count,
                    after_match_window=after_match_window,
                ):
                    usable, evidence = date_strength(str(line["raw"]))
                    if not usable:
                        continue
                    candidates.append(
                        {
                            "date_text": line["raw"],
                            "basis": basis,
                            "evidence": evidence,
                            "book_id": book_id,
                            "printed_page": printed_page,
                            "scan_page": page.get("scan_page"),
                            "page_region_source": source_name,
                            "page_line_index": line["line_index"],
                            "matched_body_line_count": len(matches),
                            "last_matched_body_line": max(
                                (int(match["body_line_index"]) for match in matches),
                                default=None,
                            ),
                        }
                    )

    candidates.sort(
        key=lambda row: (
            row["basis"] != "after_last_matched_body_line",
            -int(row["matched_body_line_count"] or 0),
            row["printed_page"],
            row["page_region_source"],
            row["page_line_index"],
        )
    )
    status = "no_date_candidate"
    if candidates:
        status = "review_date_candidate"
        if candidates[0]["basis"] == "after_last_matched_body_line" and int(candidates[0]["matched_body_line_count"] or 0) >= 2:
            status = "strong_review_date_candidate"
    return {
        "filename": filename,
        "poem_id": poem.get("id"),
        "title_bn": poem.get("title_bn"),
        "source_edition": poem.get("source_edition"),
        "source_year": poem.get("source_year"),
        "composition_date_bn": poem.get("composition_date_bn"),
        "composition_place_bn": poem.get("composition_place_bn"),
        "status": status,
        "pages_checked": pages_checked,
        "candidate_count": len(candidates),
        "candidates": candidates[:8],
    }


def markdown_report(rows: list[dict[str, Any]], max_rows: int) -> str:
    summary = Counter(row["status"] for row in rows)
    lines = [
        "# Composition Date Audit",
        "",
        "Review-only candidate dates found near cited printed page spans.",
        "",
        "## Summary",
        "",
    ]
    for status, count in summary.most_common():
        lines.append(f"- `{status}`: {count}")
    lines.extend(
        [
            "",
            "## Candidate Rows",
            "",
            "| file | title | status | best candidate | page | evidence |",
            "|---|---|---|---|---|---|",
        ]
    )
    candidate_rows = [row for row in rows if row.get("candidates")]
    for row in candidate_rows[:max_rows]:
        candidate = row["candidates"][0]
        page = f"{candidate.get('book_id')} p.{candidate.get('printed_page')} scan {candidate.get('scan_page')}"
        evidence = ", ".join(candidate.get("evidence") or [])
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or ""),
                    f"`{row.get('status')}`",
                    str(candidate.get("date_text") or "").replace("|", "\\|"),
                    page,
                    evidence,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit possible composition dates near cited poem pages.")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default=DEFAULT_DUPLICATES_SOURCE)
    parser.add_argument("--page-corpus", default=DEFAULT_PAGE_CORPUS)
    parser.add_argument("--include-untrusted-pages", action="store_true")
    parser.add_argument("--min-body-line-chars", type=int, default=14)
    parser.add_argument("--tail-line-count", type=int, default=5)
    parser.add_argument("--after-match-window", type=int, default=5)
    parser.add_argument("--output", default="metadata_reports/composition-date-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/composition-date-audit.current.md")
    parser.add_argument("--max-rows", type=int, default=120)
    args = parser.parse_args()

    poems = load_public_poems(Path(args.poems_dir), duplicate_ids(Path(args.duplicates_source)))
    page_index = load_page_index(Path(args.page_corpus), trusted_only=not args.include_untrusted_pages)
    rows = [
        audit_poem(
            filename,
            poem,
            page_index,
            min_body_line_chars=args.min_body_line_chars,
            tail_line_count=args.tail_line_count,
            after_match_window=args.after_match_window,
        )
        for filename, poem in poems
        if not poem.get("composition_date_bn")
    ]
    rows.sort(
        key=lambda row: (
            row["status"] != "strong_review_date_candidate",
            row["status"] != "review_date_candidate",
            row.get("filename") or "",
        )
    )
    payload = {
        "summary": {
            "poem_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows).most_common()),
            "note": "Review-only report; it does not mutate poem JSON.",
        },
        "matches": rows,
    }
    write_json(Path(args.output), payload)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_report(rows, args.max_rows), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
