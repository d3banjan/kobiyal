#!/usr/bin/env python3
"""Audit exact phrase-window matches between poem text and OCR page corpus.

This is review-only. It writes sidecar reports and never mutates poem JSON.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import propose_poem_spans as spans

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for direct python without uv.
    tqdm = None


def progress_iter(iterable, **kwargs):
    if tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


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
    for path in sorted(poems_dir.glob("*.json")):
        poem = read_json(path)
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if poem.get("id") in duplicates:
            continue
        poems.append((path.name, poem))
    return poems


def normalized_tokens(text: str) -> list[str]:
    return [token for token in spans.normalize(text).split() if len(token) >= 2 and not token.isdigit()]


def phrase_windows(token_list: list[str], sizes: list[int]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for size in sizes:
        if len(token_list) < size:
            continue
        for start in range(0, len(token_list) - size + 1):
            phrase = " ".join(token_list[start : start + size])
            key = (start, size, phrase)
            if key in seen:
                continue
            seen.add(key)
            windows.append({"start": start, "size": size, "phrase": phrase})
    return windows


def prepare_pages(page_corpus: Path, sizes: list[int], trusted_only: bool) -> tuple[list[dict[str, Any]], dict[str, set[int]]]:
    page_rows = []
    phrase_to_pages: dict[str, set[int]] = defaultdict(set)
    for row in read_jsonl(page_corpus):
        row = spans.prepare_page(row)
        if trusted_only and row.get("page_type") not in spans.TRUSTED_PAGE_TYPES:
            continue
        page_index = len(page_rows)
        token_list = normalized_tokens(spans.page_text(row))
        page_phrases = {window["phrase"] for window in phrase_windows(token_list, sizes)}
        row["_phrase_count"] = len(page_phrases)
        page_rows.append(row)
        for phrase in page_phrases:
            phrase_to_pages[phrase].add(page_index)
    return page_rows, phrase_to_pages


def summarize_hits(page: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    size_counts = Counter(int(hit["size"]) for hit in hits)
    covered_positions = set()
    for hit in hits:
        start = int(hit["start"])
        size = int(hit["size"])
        covered_positions.update(range(start, start + size))

    sample = []
    seen = set()
    for hit in sorted(hits, key=lambda item: (-int(item["size"]), int(item["start"]))):
        phrase = str(hit["phrase"])
        if phrase in seen:
            continue
        seen.add(phrase)
        sample.append(phrase)
        if len(sample) >= 8:
            break

    score = sum(int(hit["size"]) for hit in hits)
    return {
        "candidate_book_id": page.get("book_id"),
        "candidate_pdf_file": page.get("pdf_file"),
        "scan_page": page.get("scan_page"),
        "printed_page": page.get("printed_page_fixed"),
        "page_type": page.get("page_type"),
        "score": score,
        "covered_token_count": len(covered_positions),
        "window_counts": {str(size): size_counts[size] for size in sorted(size_counts, reverse=True)},
        "sample_phrases": sample,
    }


def row_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    best = row.get("best_page") or {}
    counts = best.get("window_counts") or {}
    return (
        int(counts.get("6") or 0),
        int(counts.get("5") or 0),
        int(counts.get("4") or 0),
        int(best.get("covered_token_count") or 0),
        int(best.get("score") or 0),
    )


def markdown_report(rows: list[dict[str, Any]], max_rows: int) -> str:
    lines = [
        "# Phrase Window Audit",
        "",
        "Review-only exact phrase-window matches between poem bodies and OCR page corpus.",
        "",
        f"- Rows with matches: {len(rows)}",
        "",
        "| file | title | current source | best page | score | windows | sample |",
        "|---|---|---|---|---:|---|---|",
    ]
    for row in rows[:max_rows]:
        best = row.get("best_page") or {}
        page = best.get("printed_page")
        page_label = str(page) if isinstance(page, int) else "পৃষ্ঠা নেই"
        page_summary = f"{best.get('candidate_book_id')}; {page_label}; scan {best.get('scan_page')}"
        windows = ", ".join(f"{size}:{count}" for size, count in (best.get("window_counts") or {}).items())
        sample = " / ".join((best.get("sample_phrases") or [])[:2])
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or ""),
                    str(row.get("source_edition") or ""),
                    page_summary,
                    str(best.get("score") or 0),
                    windows,
                    sample,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit exact phrase-window matches against OCR page corpus.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.layout.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument("--output", default="metadata_reports/phrase-window-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/phrase-window-audit.current.md")
    parser.add_argument("--sizes", default="6,5,4", help="Comma-separated token window sizes.")
    parser.add_argument("--max-page-df", type=int, default=2, help="Ignore phrases found on more than this many pages.")
    parser.add_argument("--min-score", type=int, default=20)
    parser.add_argument("--max-rows", type=int, default=80)
    parser.add_argument("--ocr-substitutions", default=None)
    parser.add_argument("--include-existing-pages", action="store_true")
    parser.add_argument("--include-untrusted-pages", action="store_true")
    parser.add_argument("--all-books", action="store_true")
    parser.add_argument("--include-logical-aliases", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    sizes = sorted({int(part) for part in args.sizes.split(",") if part.strip()}, reverse=True)
    if not sizes:
        raise ValueError("At least one phrase-window size is required.")

    spans.OCR_SUBSTITUTIONS = spans.load_ocr_substitutions(
        Path(args.ocr_substitutions) if args.ocr_substitutions else None
    )
    page_rows, phrase_to_pages = prepare_pages(
        Path(args.page_corpus),
        sizes,
        trusted_only=not args.include_untrusted_pages,
    )

    duplicates = duplicate_ids(Path(args.duplicates_source))
    poems = load_public_jibanananda_poems(Path(args.poems_dir), duplicates)
    poem_iter = poems
    if not args.no_progress:
        poem_iter = progress_iter(
            poems,
            desc="phrase windows",
            unit="poem",
            dynamic_ncols=True,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} poems [{elapsed}<{remaining}, {rate_fmt}]",
        )

    rows: list[dict[str, Any]] = []
    for filename, poem in poem_iter:
        if not args.include_existing_pages and has_primary_printed_pages(poem):
            continue
        token_list = normalized_tokens(poem.get("body_bn") or "")
        poem_windows = phrase_windows(token_list, sizes)
        allowed_books = spans.candidate_books(
            poem,
            all_books=args.all_books,
            include_logical_aliases=args.include_logical_aliases,
        )
        hits_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        skipped_common = 0
        for window in poem_windows:
            pages = phrase_to_pages.get(str(window["phrase"]))
            if not pages:
                continue
            if len(pages) > args.max_page_df:
                skipped_common += 1
                continue
            for page_index in pages:
                page = page_rows[page_index]
                if page.get("book_id") not in allowed_books:
                    continue
                hits_by_page[page_index].append(window)

        page_hits = [
            summarize_hits(page_rows[page_index], hits)
            for page_index, hits in hits_by_page.items()
        ]
        page_hits.sort(
            key=lambda item: (
                int((item.get("window_counts") or {}).get("6") or 0),
                int((item.get("window_counts") or {}).get("5") or 0),
                int((item.get("window_counts") or {}).get("4") or 0),
                int(item.get("covered_token_count") or 0),
                int(item.get("score") or 0),
            ),
            reverse=True,
        )
        if not page_hits or int(page_hits[0].get("score") or 0) < args.min_score:
            continue
        rows.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_edition": poem.get("source_edition"),
                "source_year": poem.get("source_year"),
                "source_url": poem.get("source_url"),
                "poem_token_count": len(token_list),
                "phrase_window_count": len(poem_windows),
                "skipped_common_phrase_count": skipped_common,
                "best_page": page_hits[0],
                "top_pages": page_hits[:5],
            }
        )

    rows.sort(key=row_sort_key, reverse=True)
    payload = {
        "summary": {
            "poem_count": len(poems),
            "page_count": len(page_rows),
            "matched_poem_count": len(rows),
            "sizes": sizes,
            "max_page_df": args.max_page_df,
            "min_score": args.min_score,
            "include_existing_pages": args.include_existing_pages,
            "all_books": args.all_books,
            "include_logical_aliases": args.include_logical_aliases,
        },
        "matches": rows,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_output = Path(args.markdown_output)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(markdown_report(rows, args.max_rows), encoding="utf-8")

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
