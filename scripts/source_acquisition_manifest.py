#!/usr/bin/env python3
"""Export the remaining source/corpus acquisition queue.

This sidecar is intentionally review-only. It does not mutate poem JSON. The
goal is to turn the current metadata gap report into a complete acquisition and
manual-review manifest, including unknown-source rows that are abbreviated in
the human gap summary.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


UNKNOWN_COLLECTION = "সংকলন অজানা"
SOURCE_BOOK_ID_HINTS = {
    "ঝরা পালক": "jhara-palak",
    "ধূসর পাণ্ডুলিপি": "dhusar-pandulipi",
    "বনলতা সেন": "banalata-sen",
    "মহাপৃথিবী": "mahaprithibi",
    "সাতটি তারার তিমির": "satti-tarar-timir",
    "রূপসী বাংলা": "rupasi-bangla",
    "বেলা অবেলা কালবেলা": "bela-abela-kalabela",
    "শ্রেষ্ঠ কবিতা": "srestha-kabita",
    "আলোপৃথিবী": "aloprithibi",
}


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def duplicate_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(re.findall(r'"(jibanananda-[^"]+)"', path.read_text(encoding="utf-8")))


def public_jibanananda_poems(poems_dir: Path, duplicates: set[str]) -> dict[str, dict[str, Any]]:
    poems = {}
    for path in sorted(poems_dir.glob("*.json")):
        poem = read_json(path)
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if poem.get("id") in duplicates:
            continue
        poems[path.name] = poem
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


def has_primary_printed_pages(poem: dict[str, Any]) -> bool:
    return bool(primary_printed_sources(poem))


def factor_rows_by_file(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = read_json(path)
    return {str(row["filename"]): row for row in payload.get("rows") or [] if row.get("filename")}


def page_label(start: Any, end: Any) -> str | None:
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return str(start) if start == end else f"{start}-{end}"


def candidate_summary(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate or candidate.get("status") == "no_candidate":
        return None
    return {
        "book_id": candidate.get("candidate_book_id"),
        "printed_page_start": candidate.get("printed_page_start"),
        "printed_page_end": candidate.get("printed_page_end"),
        "printed_page_label": page_label(candidate.get("printed_page_start"), candidate.get("printed_page_end")),
        "status": candidate.get("status"),
        "span_basis": candidate.get("span_basis"),
        "score": candidate.get("score"),
        "runner_up_gap": candidate.get("runner_up_gap"),
        "line_match_count": candidate.get("span_line_match_count"),
        "exact_line_match_count": candidate.get("span_exact_line_match_count"),
        "evidence": candidate.get("evidence") or [],
    }


def source_scan_summary(source_scan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not source_scan:
        return None
    best = source_scan.get("best_page") or {}
    return {
        "status": source_scan.get("status"),
        "source_edition": source_scan.get("source_edition"),
        "reason": source_scan.get("reason"),
        "candidate_book_ids": source_scan.get("candidate_book_ids") or [],
        "best_page": {
            "book_id": best.get("candidate_book_id"),
            "printed_page": best.get("candidate_printed_page"),
            "page_type": best.get("candidate_page_type"),
            "title_match": best.get("title_match"),
            "first_line_match": best.get("first_line_match"),
            "line_match_count": best.get("line_match_count"),
            "body_coverage": best.get("body_coverage"),
        }
        if best
        else None,
    }


def item_from_missing(item: dict[str, Any], factor: dict[str, Any] | None) -> dict[str, Any]:
    review_exclusion = item.get("review_exclusion") or {}
    row = {
        "filename": item.get("filename"),
        "poem_id": item.get("poem_id"),
        "title_bn": item.get("title_bn"),
        "source_edition": item.get("source_edition"),
        "book_id": SOURCE_BOOK_ID_HINTS.get(str(item.get("source_edition") or "")),
        "source_year": item.get("source_year"),
        "source_url": item.get("source_url"),
        "review_bucket": item.get("review_bucket"),
        "candidate": candidate_summary(item.get("candidate")),
        "source_scan": source_scan_summary(item.get("source_scan_review")),
        "review_exclusion": {
            "reason": review_exclusion.get("reason"),
            "note_bn": review_exclusion.get("note_bn"),
            "candidate_book_id": review_exclusion.get("candidate_book_id"),
            "candidate_page_start": review_exclusion.get("candidate_page_start"),
            "candidate_page_end": review_exclusion.get("candidate_page_end"),
        }
        if review_exclusion
        else None,
    }
    if factor:
        row["factor_model"] = {
            "next_action": factor.get("next_action"),
            "posterior_like": factor.get("posterior_like"),
            "blockers": factor.get("blockers") or [],
            "candidate_book_id": factor.get("candidate_book_id"),
            "printed_page_start": factor.get("printed_page_start"),
            "printed_page_end": factor.get("printed_page_end"),
            "stage_deltas": factor.get("stage_deltas") or {},
        }
    return row


def group_missing_items(
    missing: list[dict[str, Any]], factors: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    groups = {
        "identify_collection_before_page_citation": [],
        "add_scan_or_direct_print_review": [],
        "improve_text_extraction_or_manual_page_review": [],
        "keep_excluded_until_new_scan_or_print_review": [],
    }
    for item in missing:
        factor = factors.get(str(item.get("filename") or ""))
        review_bucket = str(item.get("review_bucket") or "")
        source_scan_status = str((item.get("source_scan_review") or {}).get("status") or "")
        source_edition = item.get("source_edition")

        # Acquisition actions intentionally mirror report_metadata_gaps'
        # source_corpus_backlog. A reviewed false candidate can still need source
        # identification or a missing source corpus before any future citation is
        # possible; only reviewed weak/no-support scans become hold rows.
        if source_edition == UNKNOWN_COLLECTION:
            key = "identify_collection_before_page_citation"
        elif source_scan_status in {"unscanned_source_edition", "no_source_scan_pages"}:
            key = "add_scan_or_direct_print_review"
        elif source_scan_status in {"source_scan_weak", "source_scan_no_support"} and review_bucket.startswith(
            "reviewed_"
        ):
            key = "keep_excluded_until_new_scan_or_print_review"
        elif source_scan_status in {"source_scan_weak", "source_scan_no_support"}:
            key = "improve_text_extraction_or_manual_page_review"
        else:
            key = "improve_text_extraction_or_manual_page_review"

        groups[key].append(item_from_missing(item, factor))
    return groups


def existing_corpus_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for group in report.get("source_corpus_backlog") or []:
        if group.get("kind") != "missing_corpus_for_existing_citations":
            continue
        for item in group.get("items") or []:
            rows.append(
                {
                    "filename": item.get("filename"),
                    "poem_id": item.get("poem_id"),
                    "title_bn": item.get("title_bn"),
                    "source_edition": group.get("source_edition"),
                    "book_id": group.get("book_id"),
                    "page_start": item.get("page_start"),
                    "page_end": item.get("page_end"),
                    "page_label": page_label(item.get("page_start"), item.get("page_end")),
                }
            )
    return rows


def group_by_source(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        source = str(item.get("source_edition") or "")
        book_id = str(item.get("book_id") or SOURCE_BOOK_ID_HINTS.get(source) or "")
        key = (source, book_id)
        row = grouped.setdefault(
            key,
            {
                "source_edition": source,
                "book_id": book_id or None,
                "count": 0,
                "items": [],
            },
        )
        row["count"] += 1
        row["items"].append(item)
    return sorted(grouped.values(), key=lambda row: (-int(row["count"]), row["source_edition"]))


def build_manifest(gap_report: dict[str, Any], factors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing = list(gap_report.get("missing") or [])
    grouped_missing = group_missing_items(missing, factors)
    existing_items = existing_corpus_items(gap_report)
    action_counts = Counter({key: len(value) for key, value in grouped_missing.items()})
    if existing_items:
        action_counts["add_corpus_or_regenerate_auxiliary_corpus"] = len(existing_items)
    factor_next_action_counts: Counter[str] = Counter(
        str(row.get("next_action") or "unknown") for row in factors.values()
    )

    source_groups = {
        key: group_by_source(items)
        for key, items in grouped_missing.items()
    }
    return {
        "summary": {
            "input_missing_printed_page_count": len(missing),
            "existing_citations_missing_corpus_count": len(existing_items),
            "action_counts": dict(action_counts),
            "factor_next_action_counts": dict(factor_next_action_counts),
            "source_group_counts": {key: len(value) for key, value in source_groups.items()},
            "generated_from": {
                "metadata_gap_report": "metadata_reports/metadata-gap-review.current.json",
                "citation_factor_model": "metadata_reports/citation-factor-model.current.json",
            },
            "note": "Review/acquisition manifest only. It does not mutate poem JSON or assert page citations.",
        },
        "existing_citations_missing_corpus": group_by_source(existing_items),
        "missing_page_citation_groups": source_groups,
    }


def sample_titles(items: list[dict[str, Any]], limit: int = 8) -> str:
    titles = [str(item.get("title_bn") or "") for item in items[:limit]]
    suffix = "" if len(items) <= limit else f" · +{len(items) - limit} more"
    return " · ".join(titles) + suffix


def markdown_report(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# Source Acquisition Manifest",
        "",
        "Generated from current metadata gap and factor-model reports.",
        "",
        "This is a review/acquisition queue only. It does not create page citations.",
        "",
        "## Summary",
        "",
        f"- Missing printed-page citations: {summary['input_missing_printed_page_count']}",
        f"- Existing citations missing corpus coverage: {summary['existing_citations_missing_corpus_count']}",
        f"- Action counts: `{json.dumps(summary['action_counts'], ensure_ascii=False)}`",
        f"- Factor-model next actions: `{json.dumps(summary['factor_next_action_counts'], ensure_ascii=False)}`",
        f"- Source group counts: `{json.dumps(summary['source_group_counts'], ensure_ascii=False)}`",
        "",
        "## Existing Citations Missing Corpus",
        "",
        "| source edition | book id | count | page citations | titles |",
        "|---|---|---:|---|---|",
    ]
    existing_groups = manifest.get("existing_citations_missing_corpus") or []
    if not existing_groups:
        lines.append("| - | - | 0 | - | - |")
    for group in existing_groups:
        pages = " · ".join(
            f"{item.get('poem_id')} p.{item.get('page_label')}" for item in group.get("items") or []
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(group.get("source_edition") or ""),
                    str(group.get("book_id") or ""),
                    str(group.get("count") or 0),
                    pages.replace("|", "\\|"),
                    sample_titles(group.get("items") or []).replace("|", "\\|"),
                ]
            )
            + " |"
        )

    action_labels = [
        ("add_scan_or_direct_print_review", "Add Scan Or Direct Print Review"),
        ("identify_collection_before_page_citation", "Identify Collection Before Page Citation"),
        ("improve_text_extraction_or_manual_page_review", "Improve Text Extraction Or Manual Review"),
        ("keep_excluded_until_new_scan_or_print_review", "Reviewed Holds"),
    ]
    groups = manifest.get("missing_page_citation_groups") or {}
    for key, label in action_labels:
        lines.extend(
            [
                "",
                f"## {label}",
                "",
                "| source edition | book id | count | sample titles |",
                "|---|---|---:|---|",
            ]
        )
        rows = groups.get(key) or []
        if not rows:
            lines.append("| - | - | 0 | - |")
            continue
        for group in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(group.get("source_edition") or ""),
                        str(group.get("book_id") or ""),
                        str(group.get("count") or 0),
                        sample_titles(group.get("items") or []).replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def manifest_group_items(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for key, source_groups in (manifest.get("missing_page_citation_groups") or {}).items():
        items = []
        for source_group in source_groups or []:
            group_items = source_group.get("items") or []
            expected_count = source_group.get("count")
            if expected_count != len(group_items):
                raise ValueError(
                    f"{key} source group {source_group.get('source_edition')} count "
                    f"{expected_count} != {len(group_items)}"
                )
            items.extend(group_items)
        grouped[key] = items
    return grouped


def flatten_existing_corpus_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for source_group in manifest.get("existing_citations_missing_corpus") or []:
        group_items = source_group.get("items") or []
        expected_count = source_group.get("count")
        if expected_count != len(group_items):
            raise ValueError(
                f"existing corpus group {source_group.get('source_edition')} count "
                f"{expected_count} != {len(group_items)}"
            )
        rows.extend(group_items)
    return rows


def verify_manifest(manifest_path: Path, poems_dir: Path, duplicates_source: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    summary = manifest.get("summary") or {}
    grouped_items = manifest_group_items(manifest)
    missing_items = [item for items in grouped_items.values() for item in items]
    existing_items = flatten_existing_corpus_items(manifest)

    action_counts = summary.get("action_counts") or {}
    for key, items in grouped_items.items():
        if int(action_counts.get(key, -1)) != len(items):
            raise ValueError(f"action_counts[{key}] != grouped item count {len(items)}")
    if int(action_counts.get("add_corpus_or_regenerate_auxiliary_corpus", -1)) != len(existing_items):
        raise ValueError("existing-corpus action count does not match existing citation rows")

    if int(summary.get("input_missing_printed_page_count", -1)) != len(missing_items):
        raise ValueError("summary missing printed-page count does not match manifest rows")
    if int(summary.get("existing_citations_missing_corpus_count", -1)) != len(existing_items):
        raise ValueError("summary existing-corpus count does not match manifest rows")

    source_group_counts = summary.get("source_group_counts") or {}
    for key, source_groups in (manifest.get("missing_page_citation_groups") or {}).items():
        if int(source_group_counts.get(key, -1)) != len(source_groups or []):
            raise ValueError(f"source_group_counts[{key}] does not match manifest groups")

    filenames = [str(item.get("filename") or "") for item in missing_items]
    duplicates = [name for name, count in Counter(filenames).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate missing-page manifest rows: {', '.join(sorted(duplicates))}")

    poems = public_jibanananda_poems(poems_dir, duplicate_ids(duplicates_source))
    missing_from_poems = {
        filename
        for filename, poem in poems.items()
        if not has_primary_printed_pages(poem)
    }
    manifest_missing = set(filenames)
    if manifest_missing != missing_from_poems:
        missing_in_manifest = sorted(missing_from_poems - manifest_missing)
        extra_in_manifest = sorted(manifest_missing - missing_from_poems)
        raise ValueError(
            "manifest rows do not match public poems lacking primary printed pages: "
            f"missing={missing_in_manifest[:12]} extra={extra_in_manifest[:12]}"
        )

    unknown_items = grouped_items.get("identify_collection_before_page_citation") or []
    unknown_from_poems = {
        filename
        for filename in missing_from_poems
        if poems[filename].get("source_edition") == UNKNOWN_COLLECTION
    }
    if {str(item.get("filename") or "") for item in unknown_items} != unknown_from_poems:
        raise ValueError("unknown-source manifest rows do not match poem JSON")

    known_missing_count = len(missing_from_poems) - len(unknown_from_poems)
    known_action_count = sum(
        len(grouped_items.get(key) or [])
        for key in (
            "add_scan_or_direct_print_review",
            "improve_text_extraction_or_manual_page_review",
            "keep_excluded_until_new_scan_or_print_review",
        )
    )
    if known_action_count != known_missing_count:
        raise ValueError("known-source action rows do not account for known-source missing-page poems")

    for item in missing_items:
        filename = str(item.get("filename") or "")
        poem = poems[filename]
        for key in ("poem_id", "title_bn", "source_edition", "source_year", "source_url"):
            if item.get(key) != poem.get("id" if key == "poem_id" else key):
                raise ValueError(f"{filename} manifest {key} does not match poem JSON")

    for item in existing_items:
        filename = str(item.get("filename") or "")
        poem = poems.get(filename)
        if poem is None:
            raise ValueError(f"existing-corpus row points to non-public poem: {filename}")
        source_matches = [
            source
            for source in primary_printed_sources(poem)
            if source.get("title_bn") == item.get("source_edition")
            and source.get("page_start") == item.get("page_start")
            and source.get("page_end") == item.get("page_end")
        ]
        if not source_matches:
            raise ValueError(f"existing-corpus row does not match primary source pages: {filename}")

    result = {
        "verified": True,
        "public_jibanananda_poem_count": len(poems),
        "missing_printed_page_count": len(missing_items),
        "unknown_collection_missing_count": len(unknown_items),
        "known_source_missing_page_count": known_missing_count,
        "existing_citations_missing_corpus_count": len(existing_items),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Export source/corpus acquisition manifest.")
    parser.add_argument("--gap-report", default="metadata_reports/metadata-gap-review.current.json")
    parser.add_argument("--factor-model", default="metadata_reports/citation-factor-model.current.json")
    parser.add_argument("--output", default="metadata_reports/source-acquisition-manifest.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/source-acquisition-manifest.current.md")
    parser.add_argument(
        "--verify-manifest",
        help="Verify an existing manifest against current poem JSON instead of generating one.",
    )
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    args = parser.parse_args()

    if args.verify_manifest:
        verify_manifest(Path(args.verify_manifest), Path(args.poems_dir), Path(args.duplicates_source))
        return 0

    gap_report = read_json(Path(args.gap_report))
    factors = factor_rows_by_file(Path(args.factor_model) if args.factor_model else None)
    manifest = build_manifest(gap_report, factors)
    write_json(Path(args.output), manifest)
    Path(args.markdown_output).write_text(markdown_report(manifest), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
