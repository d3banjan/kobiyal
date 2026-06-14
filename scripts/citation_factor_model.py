#!/usr/bin/env python3
"""Rank unresolved citation candidates with review-only posterior factors.

This is not a calibrated statistical model yet. It is a transparent factor
ledger that treats each pipeline transformation as evidence that adjusts a
candidate's log odds. The output is for triage and review; it never mutates poem
JSON.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import apply_poem_metadata as apply_meta


UNKNOWN_COLLECTION = "সংকলন অজানা"
INITIAL_LOG_ODDS = -3.0


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sigmoid(log_odds: float) -> float:
    return 1 / (1 + math.exp(-log_odds))


def rounded_posterior(log_odds: float) -> float:
    return round(sigmoid(log_odds), 4)


def canonical_book_id(book_id: str | None) -> str | None:
    if not book_id:
        return None
    return apply_meta.BOOK_ALIASES.get(book_id, book_id)


def source_book_id(source_edition: str | None) -> str | None:
    if not source_edition or source_edition == UNKNOWN_COLLECTION:
        return None
    for book_id, meta in apply_meta.BASE_BOOK_META.items():
        if meta.get("title_bn") == source_edition:
            return book_id
    return None


def add_factor(
    factors: list[dict[str, Any]],
    stage: str,
    name: str,
    weight: float,
    observation: Any,
    note: str,
) -> None:
    factors.append(
        {
            "stage": stage,
            "name": name,
            "weight": round(weight, 3),
            "observation": observation,
            "note": note,
        }
    )


def stage_updates(factors: list[dict[str, Any]], initial_log_odds: float) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for factor in factors:
        grouped.setdefault(str(factor["stage"]), []).append(factor)

    updates = []
    current = initial_log_odds
    for stage, stage_factors in grouped.items():
        before = current
        delta = sum(float(factor["weight"]) for factor in stage_factors)
        current += delta
        updates.append(
            {
                "stage": stage,
                "factor_count": len(stage_factors),
                "delta_log_odds": round(delta, 3),
                "posterior_before": rounded_posterior(before),
                "posterior_after": rounded_posterior(current),
                "factors": [factor["name"] for factor in stage_factors],
            }
        )
    return updates


def candidate_book_alignment(source_edition: str | None, candidate_book_id: str | None) -> str:
    if not candidate_book_id:
        return "no_candidate_book"
    candidate_canonical = canonical_book_id(candidate_book_id)
    source_book = source_book_id(source_edition)
    if source_edition == UNKNOWN_COLLECTION:
        return "unknown_source"
    if source_book is None:
        return "unmapped_source"
    if candidate_canonical == source_book:
        return "same_source_book"
    return "source_conflict"


def score_item(item: dict[str, Any]) -> dict[str, Any]:
    candidate = item.get("candidate") or {}
    source_scan = item.get("source_scan_review") or {}
    review_exclusion = item.get("review_exclusion")
    source_edition = item.get("source_edition")
    candidate_book = candidate.get("candidate_book_id")
    factors: list[dict[str, Any]] = []
    log_odds = INITIAL_LOG_ODDS

    if review_exclusion:
        add_factor(
            factors,
            "human_review",
            "reviewed_negative",
            -8.0,
            review_exclusion.get("reason"),
            "A reviewed exclusion is strong negative evidence.",
        )

    if source_edition == UNKNOWN_COLLECTION:
        add_factor(
            factors,
            "source_prior",
            "unknown_collection",
            -0.9,
            source_edition,
            "Unknown collection lowers the prior until source identity is reviewed.",
        )
    elif source_book_id(str(source_edition)) is None:
        add_factor(
            factors,
            "source_prior",
            "unmapped_source_collection",
            -0.6,
            source_edition,
            "The source edition is known but absent from the primary book map.",
        )
    else:
        add_factor(
            factors,
            "source_prior",
            "known_collection",
            0.8,
            source_edition,
            "Known source edition gives the candidate a collection prior.",
        )

    alignment = candidate_book_alignment(source_edition, candidate_book)
    alignment_weights = {
        "same_source_book": 1.8,
        "no_candidate_book": 0.0,
        "unknown_source": 0.0,
        "unmapped_source": -0.4,
        "source_conflict": -2.2,
    }
    if candidate_book:
        add_factor(
            factors,
            "source_prior",
            "candidate_source_alignment",
            alignment_weights[alignment],
            {"source_edition": source_edition, "candidate_book_id": candidate_book, "alignment": alignment},
            "Candidate book is compared against the current source-edition prior.",
        )

    page_start = candidate.get("printed_page_start")
    page_end = candidate.get("printed_page_end")
    if isinstance(page_start, int) and isinstance(page_end, int):
        add_factor(
            factors,
            "page_sequence_repair",
            "printed_page_available",
            1.2,
            {"page_start": page_start, "page_end": page_end},
            "A repaired printed page span exists, so this can be checked as a book citation.",
        )
    elif candidate and candidate.get("status") != "no_candidate":
        add_factor(
            factors,
            "page_sequence_repair",
            "missing_printed_page",
            -2.0,
            None,
            "A citation cannot be written without a printed page number.",
        )

    evidence = set(candidate.get("evidence") or [])
    if "page_sequence_present" in evidence:
        add_factor(
            factors,
            "page_sequence_repair",
            "page_sequence_present",
            0.9,
            sorted(evidence),
            "The page candidate sits in a repaired printed-page sequence.",
        )

    span_basis = candidate.get("span_basis")
    if span_basis == "line_anchor_cluster":
        add_factor(
            factors,
            "text_alignment",
            "line_anchor_cluster",
            1.6,
            span_basis,
            "Candidate span is derived from clustered line anchors.",
        )
    elif span_basis:
        add_factor(
            factors,
            "text_alignment",
            "non_anchor_span_basis",
            -0.9,
            span_basis,
            "Token/title-only spans are not strong enough for citation writes.",
        )

    exact_count = int(candidate.get("span_exact_line_match_count") or 0)
    line_count = int(candidate.get("span_line_match_count") or 0)
    if exact_count:
        add_factor(
            factors,
            "text_alignment",
            "exact_line_matches",
            min(2.4, exact_count * 0.45),
            exact_count,
            "Exact line anchors raise posterior more than fuzzy token overlap.",
        )
    if line_count:
        add_factor(
            factors,
            "text_alignment",
            "line_matches",
            min(1.4, line_count * 0.18),
            line_count,
            "Line-level agreement is positive but less decisive than exact anchors.",
        )
    if "title_match" in evidence:
        add_factor(factors, "text_alignment", "title_match", 0.8, True, "Title evidence is useful but collision-prone.")
    if "first_line_match" in evidence:
        add_factor(factors, "text_alignment", "first_line_match", 1.1, True, "Opening-line evidence is a strong anchor.")
    if "last_line_match" in evidence:
        add_factor(factors, "text_alignment", "last_line_match", 0.7, True, "Last-line evidence helps identify a span end.")
    if "high_body_coverage" in evidence:
        add_factor(
            factors,
            "text_alignment",
            "high_body_coverage",
            0.7,
            True,
            "Broad body-token coverage supports but does not prove a citation.",
        )

    runner_gap = candidate.get("runner_up_gap")
    if isinstance(runner_gap, (int, float)):
        if runner_gap >= 4:
            weight = 0.7
        elif runner_gap >= 1:
            weight = 0.25
        else:
            weight = -0.7
        add_factor(
            factors,
            "competition",
            "runner_up_gap",
            weight,
            runner_gap,
            "Low runner-up separation keeps ambiguous candidates in review.",
        )

    status = str(candidate.get("status") or "no_candidate")
    status_weights = {
        "accepted_candidate": 1.4,
        "needs_manual_review": 0.2,
        "ambiguous": -0.8,
        "no_candidate": -2.5,
    }
    add_factor(
        factors,
        "candidate_generation",
        "candidate_status",
        status_weights.get(status, -0.5),
        status,
        "Generator status summarizes upstream candidate quality.",
    )

    source_scan_status = str(source_scan.get("status") or "not_applicable")
    source_scan_weights = {
        "source_scan_supported": 1.5,
        "source_scan_token_only": 0.4,
        "source_scan_weak": -0.7,
        "source_scan_no_support": -1.4,
        "unscanned_source_edition": -0.8,
        "no_source_scan_pages": -0.8,
        "not_applicable": 0.0,
    }
    add_factor(
        factors,
        "corpus_coverage",
        "source_scan_status",
        source_scan_weights.get(source_scan_status, -0.2),
        source_scan_status,
        "Known-source rows are checked against the current OCR corpus when available.",
    )

    for factor in factors:
        log_odds += float(factor["weight"])

    blockers = blockers_for(item, alignment=alignment)
    updates = stage_updates(factors, INITIAL_LOG_ODDS)
    return {
        "filename": item.get("filename"),
        "poem_id": item.get("poem_id"),
        "title_bn": item.get("title_bn"),
        "source_edition": source_edition,
        "source_year": item.get("source_year"),
        "candidate_book_id": candidate_book,
        "printed_page_start": page_start,
        "printed_page_end": page_end,
        "base_log_odds": INITIAL_LOG_ODDS,
        "base_posterior_like": rounded_posterior(INITIAL_LOG_ODDS),
        "posterior_like": rounded_posterior(log_odds),
        "log_odds": round(log_odds, 3),
        "review_bucket": item.get("review_bucket"),
        "next_action": next_action(item, blockers=blockers, posterior=sigmoid(log_odds), alignment=alignment),
        "blockers": blockers,
        "stage_updates": updates,
        "stage_deltas": {update["stage"]: update["delta_log_odds"] for update in updates},
        "factors": factors,
    }


def blockers_for(item: dict[str, Any], alignment: str) -> list[str]:
    candidate = item.get("candidate") or {}
    source_scan = item.get("source_scan_review") or {}
    blockers = []
    if item.get("review_exclusion"):
        blockers.append("reviewed_negative")
    if candidate.get("status") in {None, "no_candidate"}:
        blockers.append("no_candidate")
    if not isinstance(candidate.get("printed_page_start"), int) or not isinstance(candidate.get("printed_page_end"), int):
        blockers.append("no_printed_page")
    if candidate.get("span_basis") != "line_anchor_cluster":
        blockers.append("no_line_anchor_cluster")
    if int(candidate.get("span_exact_line_match_count") or 0) == 0:
        blockers.append("no_exact_line_anchor")
    if alignment == "source_conflict":
        blockers.append("source_conflict")
    if alignment == "unknown_source":
        blockers.append("unknown_source_collection")
    if source_scan.get("status") in {"unscanned_source_edition", "no_source_scan_pages"}:
        blockers.append("missing_source_corpus")
    return blockers


def next_action(item: dict[str, Any], blockers: list[str], posterior: float, alignment: str) -> str:
    source_scan = item.get("source_scan_review") or {}
    if "reviewed_negative" in blockers:
        return "keep_excluded"
    if source_scan.get("status") in {"unscanned_source_edition", "no_source_scan_pages"}:
        return "add_or_review_source_corpus"
    if "no_candidate" in blockers:
        return "needs_better_text_extraction"
    if "unknown_source_collection" in blockers and posterior >= 0.65:
        return "manual_collection_review"
    if "source_conflict" in blockers:
        return "manual_source_conflict_review"
    if posterior >= 0.72 and "no_line_anchor_cluster" not in blockers and "no_printed_page" not in blockers:
        return "manual_printed_page_review"
    if alignment == "unknown_source":
        return "identify_collection"
    return "needs_stronger_text_anchors"


def markdown_report(payload: dict[str, Any], max_rows: int) -> str:
    lines = [
        "# Citation Factor Model",
        "",
        "Review-only posterior-style triage for unresolved printed-page citation gaps.",
        "",
        f"- Input gaps: {payload['summary']['input_gap_count']}",
        f"- Rows scored: {payload['summary']['scored_count']}",
        f"- Next actions: `{json.dumps(payload['summary']['next_action_counts'], ensure_ascii=False)}`",
        f"- Stage deltas: `{json.dumps(payload['summary']['stage_delta_totals'], ensure_ascii=False)}`",
        "",
        "| file | title | posterior | candidate | next action | blockers | top factors |",
        "|---|---|---:|---|---|---|---|",
    ]
    for row in payload["rows"][:max_rows]:
        page_start = row.get("printed_page_start")
        page_end = row.get("printed_page_end")
        if isinstance(page_start, int) and isinstance(page_end, int):
            page = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
        else:
            page = "-"
        candidate = f"{row.get('candidate_book_id') or '-'} p.{page}"
        factors = sorted(row.get("factors") or [], key=lambda item: abs(float(item.get("weight") or 0)), reverse=True)[:4]
        top_factors = " · ".join(f"{factor['name']} {factor['weight']:+g}" for factor in factors)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or "").replace("|", "\\|"),
                    str(row.get("posterior_like")),
                    candidate,
                    str(row.get("next_action") or ""),
                    ", ".join(row.get("blockers") or []),
                    top_factors.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build review-only posterior factors for citation gaps.")
    parser.add_argument("--gap-report", default="metadata_reports/metadata-gap-review.current.json")
    parser.add_argument("--output", default="metadata_reports/citation-factor-model.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/citation-factor-model.current.md")
    parser.add_argument("--max-rows", type=int, default=140)
    args = parser.parse_args()

    gap_report = read_json(Path(args.gap_report))
    rows = [score_item(item) for item in gap_report.get("missing") or []]
    rows.sort(key=lambda row: (-float(row["posterior_like"]), row.get("filename") or ""))
    stage_delta_totals: Counter[str] = Counter()
    stage_positive_counts: Counter[str] = Counter()
    stage_negative_counts: Counter[str] = Counter()
    for row in rows:
        for update in row.get("stage_updates") or []:
            stage = str(update["stage"])
            delta = float(update["delta_log_odds"])
            stage_delta_totals[stage] += delta
            if delta > 0:
                stage_positive_counts[stage] += 1
            elif delta < 0:
                stage_negative_counts[stage] += 1
    payload = {
        "summary": {
            "input_gap_count": len(gap_report.get("missing") or []),
            "scored_count": len(rows),
            "next_action_counts": dict(Counter(str(row["next_action"]) for row in rows).most_common()),
            "stage_delta_totals": {stage: round(delta, 3) for stage, delta in stage_delta_totals.most_common()},
            "stage_positive_counts": dict(stage_positive_counts.most_common()),
            "stage_negative_counts": dict(stage_negative_counts.most_common()),
            "note": "Review-only factor ledger. Posterior-like scores are heuristic and do not mutate poem JSON.",
        },
        "rows": rows,
    }
    write_json(Path(args.output), payload)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_report(payload, args.max_rows), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
