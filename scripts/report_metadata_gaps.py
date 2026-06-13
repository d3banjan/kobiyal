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


UNKNOWN_COLLECTION = "সংকলন অজানা"
DEFAULT_REVIEW_EXCLUSIONS = "src/data/metadata-review-exclusions.json"


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


def review_bucket(poem: dict[str, Any], row: dict[str, Any] | None) -> str:
    if row is None or row.get("status") == "no_candidate":
        return "no_candidate"
    if not isinstance(row.get("printed_page_start"), int) or not isinstance(row.get("printed_page_end"), int):
        return "needs_printed_page_sequence"
    if row.get("span_basis") != "line_anchor_cluster":
        return "weak_text_anchor"
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


def review_note_summary(item: dict[str, Any]) -> str:
    exclusion = item.get("review_exclusion")
    if not exclusion:
        return ""
    return str(exclusion.get("note_bn") or exclusion.get("reason") or "")


def build_report(
    poems: list[tuple[str, dict[str, Any]]],
    candidates_by_file: dict[str, dict[str, Any]],
    review_exclusions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    missing = []
    for filename, poem in poems:
        if has_primary_printed_pages(poem):
            continue
        row = candidates_by_file.get(filename)
        review_exclusion = find_review_exclusion(filename, row, review_exclusions)
        bucket = str(review_exclusion.get("review_bucket")) if review_exclusion else review_bucket(poem, row)
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
                "review_exclusion": review_exclusion,
            }
        )

    missing.sort(
        key=lambda item: (
            item["review_bucket"].startswith("reviewed_"),
            item["review_bucket"] in {"no_candidate", "weak_text_anchor"},
            -int(item["evidence_score"]),
            item["source_edition"] or "",
            item["title_bn"] or "",
        )
    )
    return {
        "summary": {
            "public_poem_count": len(poems),
            "missing_printed_page_count": len(missing),
            "unknown_collection_missing_count": sum(
                1 for item in missing if item["source_edition"] == UNKNOWN_COLLECTION
            ),
            "missing_source_year_count": sum(1 for _, poem in poems if poem.get("source_year") is None),
            "review_buckets": dict(Counter(item["review_bucket"] for item in missing).most_common()),
            "missing_by_source_edition": dict(
                Counter(item["source_edition"] or "" for item in missing).most_common()
            ),
            "reviewed_exclusion_count": sum(1 for item in missing if item.get("review_exclusion")),
        },
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
        f"- Reviewed exclusions: {summary['reviewed_exclusion_count']}",
        "",
        "## Review buckets",
        "",
    ]
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

    lines.extend(
        [
            "",
            "## Ranked Rows",
            "",
            "| file | title | current source | review bucket | candidate | source URL |",
            "|---|---|---|---|---|---|",
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
    parser.add_argument("--candidates", default=None)
    parser.add_argument("--review-exclusions", default=DEFAULT_REVIEW_EXCLUSIONS)
    parser.add_argument("--output", default="metadata_reports/metadata-gap-review.current.md")
    parser.add_argument("--json-output", default="metadata_reports/metadata-gap-review.current.json")
    parser.add_argument("--max-rows", type=int, default=140)
    args = parser.parse_args()

    duplicates = duplicate_ids(Path(args.duplicates_source))
    poems = load_public_jibanananda_poems(Path(args.poems_dir), duplicates)
    candidates_by_file: dict[str, dict[str, Any]] = {}
    if args.candidates:
        for row in read_jsonl(Path(args.candidates)):
            filename = row.get("filename")
            if filename:
                candidates_by_file[str(filename)] = row
    review_exclusions = load_review_exclusions(Path(args.review_exclusions))

    report = build_report(poems, candidates_by_file, review_exclusions)
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
