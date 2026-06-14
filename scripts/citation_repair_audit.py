#!/usr/bin/env python3
"""Find safe repair candidates for existing printed-page citations.

This is deliberately stricter than the general citation consistency audit. A
logical copy/appendix section can prove that a poem appears somewhere, but its
printed page number may belong to a different physical book convention. This
report only promotes same-exact-book candidates as automatic repair candidates.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import apply_poem_metadata as apply_meta


ISSUE_STATUSES = {
    "candidate_conflict",
    "missing_book_corpus",
    "missing_page_rows",
    "missing_printed_page_sequence",
    "outside_corpus_range",
    "weak_current_citation",
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


def canonical_book_id(book_id: str | None) -> str | None:
    if not book_id:
        return None
    return apply_meta.BOOK_ALIASES.get(book_id, book_id)


def load_best_candidates(path: Path) -> dict[str, dict[str, Any]]:
    rows = {}
    if not path.exists():
        return rows
    for row in read_jsonl(path):
        filename = row.get("filename")
        if filename:
            rows[str(filename)] = row
    return rows


def citation_pages(row: dict[str, Any]) -> tuple[int | None, int | None]:
    citation = row.get("citation") or {}
    start = citation.get("page_start")
    end = citation.get("page_end")
    return (
        int(start) if isinstance(start, int) else None,
        int(end) if isinstance(end, int) else None,
    )


def candidate_pages(row: dict[str, Any]) -> tuple[int | None, int | None]:
    start = row.get("printed_page_start")
    end = row.get("printed_page_end")
    return (
        int(start) if isinstance(start, int) else None,
        int(end) if isinstance(end, int) else None,
    )


def has_strong_candidate_evidence(candidate: dict[str, Any]) -> bool:
    if candidate.get("status") != "accepted_candidate":
        return False
    if candidate.get("span_basis") != "line_anchor_cluster":
        return False
    if int(candidate.get("span_anchor_count") or 0) < 1:
        return False
    if int(candidate.get("span_line_match_count") or 0) < 3:
        return False
    if int(candidate.get("span_exact_line_match_count") or 0) < 1:
        return False
    return "page_sequence_present" in set(candidate.get("evidence") or [])


def classify_repair(row: dict[str, Any], candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate or not has_strong_candidate_evidence(candidate):
        return None

    current_start, current_end = citation_pages(row)
    candidate_start, candidate_end = candidate_pages(candidate)
    if candidate_start is None or candidate_end is None:
        return None
    if (candidate_start, candidate_end) == (current_start, current_end):
        return None

    source_book_id = row.get("source_book_id")
    candidate_book_id = str(candidate.get("candidate_book_id") or "")
    candidate_canonical = canonical_book_id(candidate_book_id)
    same_canonical_book = source_book_id == candidate_canonical
    same_exact_book = source_book_id == candidate_book_id
    candidate_is_alias = same_canonical_book and not same_exact_book

    if same_exact_book and row.get("status") in ISSUE_STATUSES:
        repair_status = "safe_same_book_repair_candidate"
    elif candidate_is_alias:
        repair_status = "alias_alternate_page_evidence"
    elif same_canonical_book:
        repair_status = "canonical_book_page_conflict"
    else:
        repair_status = "different_collection_candidate"

    return {
        "filename": row.get("filename"),
        "poem_id": row.get("poem_id"),
        "title_bn": row.get("title_bn"),
        "source_title_bn": row.get("source_title_bn"),
        "source_book_id": source_book_id,
        "current_status": row.get("status"),
        "current_page_start": current_start,
        "current_page_end": current_end,
        "candidate_book_id": candidate_book_id,
        "candidate_canonical_book_id": candidate_canonical,
        "candidate_is_alias": candidate_is_alias,
        "candidate_page_start": candidate_start,
        "candidate_page_end": candidate_end,
        "candidate_score": candidate.get("score"),
        "candidate_evidence": candidate.get("evidence"),
        "span_line_match_count": candidate.get("span_line_match_count"),
        "span_exact_line_match_count": candidate.get("span_exact_line_match_count"),
        "repair_status": repair_status,
    }


def markdown_report(rows: list[dict[str, Any]], max_rows: int) -> str:
    lines = [
        "# Citation Repair Audit",
        "",
        "Review-only repair candidates for existing printed-page citations.",
        "",
        f"- Rows with alternate candidates: {len(rows)}",
        f"- Status counts: `{json.dumps(dict(Counter(row['repair_status'] for row in rows)), ensure_ascii=False)}`",
        "",
        "| file | title | current | candidate | status | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows[:max_rows]:
        current = f"{row.get('source_book_id')} p.{row.get('current_page_start')}"
        if row.get("current_page_end") != row.get("current_page_start"):
            current += f"-{row.get('current_page_end')}"
        candidate = f"{row.get('candidate_book_id')} p.{row.get('candidate_page_start')}"
        if row.get("candidate_page_end") != row.get("candidate_page_start"):
            candidate += f"-{row.get('candidate_page_end')}"
        evidence = (
            f"score {row.get('candidate_score')}; "
            f"lines {row.get('span_line_match_count')}; "
            f"exact {row.get('span_exact_line_match_count')}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or "").replace("|", "\\|"),
                    current,
                    candidate,
                    str(row.get("repair_status") or ""),
                    evidence,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit safe repairs for existing printed-page citations.")
    parser.add_argument("--citation-audit", default="metadata_reports/citation-consistency-audit.current.json")
    parser.add_argument("--candidates", default="metadata_reports/poem-span-candidates.current.regen.jsonl")
    parser.add_argument("--output", default="metadata_reports/citation-repair-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/citation-repair-audit.current.md")
    parser.add_argument("--max-rows", type=int, default=120)
    args = parser.parse_args()

    citation_report = read_json(Path(args.citation_audit))
    candidates = load_best_candidates(Path(args.candidates))
    rows = []
    for row in citation_report.get("citations") or []:
        repair = classify_repair(row, candidates.get(str(row.get("filename") or "")))
        if repair is not None:
            rows.append(repair)

    rows.sort(
        key=lambda row: (
            row.get("repair_status") != "safe_same_book_repair_candidate",
            row.get("repair_status") != "canonical_book_page_conflict",
            row.get("repair_status") != "alias_alternate_page_evidence",
            row.get("filename") or "",
        )
    )
    payload = {
        "summary": {
            "citation_count": len(citation_report.get("citations") or []),
            "candidate_count": len(candidates),
            "repair_candidate_count": len(rows),
            "status_counts": dict(Counter(row["repair_status"] for row in rows).most_common()),
            "safe_same_book_repair_count": sum(
                1 for row in rows if row.get("repair_status") == "safe_same_book_repair_candidate"
            ),
            "note": "Review-only report; alias/copy-section page evidence must not overwrite linked-book page citations.",
        },
        "repairs": rows,
    }
    write_json(Path(args.output), payload)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_report(rows, args.max_rows), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
