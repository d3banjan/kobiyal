#!/usr/bin/env python3
"""Review order-aware poem/page matches against local OCR regions.

This is a diagnostic sidecar generator. It does not mutate poem JSON. It is
meant for the remaining Jibanananda metadata gaps after title, exact line,
phrase-window, and fuzzy page-wide passes have been exhausted.
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


DEFAULT_REVIEW_EXCLUSIONS = "src/data/metadata-review-exclusions.json"
UNKNOWN_COLLECTION = "সংকলন অজানা"
DEFAULT_MAX_REGION_LINES = 4
DEFAULT_MAX_REGION_CHARS = 700

CLASS_GROUPS = [
    "ািীুূৃেৈোৌ",
    "নণংঙঞ",
    "শষস",
    "যয়য়",
    "রব",
    "দধ",
    "তৎ",
    "চছ",
    "জঝ",
]
CHAR_CLASS_MAP = {
    char: chr(0xE000 + index)
    for index, group in enumerate(CLASS_GROUPS)
    for char in group
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


def progress_iter(iterable, **kwargs):
    if tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


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
    for path in sorted(poems_dir.glob("*.json")):
        poem = read_json(path)
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if poem.get("id") in duplicates:
            continue
        if has_primary_printed_pages(poem):
            continue
        poems.append((path.name, poem))
    return poems


def load_review_exclusions(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    data = read_json(path)
    exclusions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in data.get("items") or []:
        filename = item.get("filename")
        if filename:
            exclusions[str(filename)].append(item)
    return exclusions


def matches_review_exclusion(
    filename: str,
    candidate: dict[str, Any],
    review_exclusions: dict[str, list[dict[str, Any]]],
) -> bool:
    for item in review_exclusions.get(filename, []):
        if item.get("candidate_book_id") != candidate.get("candidate_book_id"):
            continue
        if "candidate_page_start" in item and item.get("candidate_page_start") != candidate.get("printed_page_start"):
            continue
        if "candidate_page_end" in item and item.get("candidate_page_end") != candidate.get("printed_page_end"):
            continue
        return True
    return False


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", spans.normalize(text))


def token_list(text: str, min_token_chars: int = 2) -> list[str]:
    normalized = spans.normalize(text)
    tokens = []
    for token in normalized.split():
        if token.isdigit() or len(compact_text(token)) >= min_token_chars:
            tokens.append(token)
    return tokens


def class_signature(text: str) -> str:
    return "".join(CHAR_CLASS_MAP.get(char, char) for char in text)


def class_tokens(tokens: list[str]) -> list[str]:
    return [class_signature(token) for token in tokens]


def profile_text(page: dict[str, Any]) -> str:
    return "\n".join(profile.get("text") or "" for profile in page.get("ocr_profiles") or [])


def page_region_sources(page: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("ocr", page.get("raw_ocr") or ""),
        ("pdftotext", page.get("raw_pdftotext") or ""),
        ("profile", profile_text(page)),
    ]


def normalized_source_lines(text: str, max_region_chars: int) -> list[str]:
    lines = []
    for raw_line in text.splitlines():
        normalized = spans.normalize(raw_line)
        compact = compact_text(normalized)
        if len(compact) < 3:
            continue
        if compact.isdigit():
            continue
        if len(compact) > max_region_chars:
            continue
        lines.append(normalized)
    return lines


def page_region_windows(
    page: dict[str, Any],
    max_region_lines: int,
    max_region_chars: int,
    min_region_tokens: int,
) -> list[dict[str, Any]]:
    windows = []
    seen = set()
    for source_name, source_text in page_region_sources(page):
        lines = normalized_source_lines(source_text, max_region_chars)
        for start in range(len(lines)):
            pieces: list[str] = []
            for end in range(start, min(len(lines), start + max_region_lines)):
                pieces.append(lines[end])
                text = " ".join(pieces)
                if len(compact_text(text)) > max_region_chars:
                    break
                tokens = token_list(text)
                if len(tokens) < min_region_tokens:
                    continue
                key = (source_name, text)
                if key in seen:
                    continue
                seen.add(key)
                token_set = set(tokens)
                cls_tokens = class_tokens(tokens)
                windows.append(
                    {
                        "source": source_name,
                        "line_start": start,
                        "line_end": end,
                        "text": text,
                        "tokens": tokens,
                        "class_tokens": cls_tokens,
                        "token_set": token_set,
                        "class_token_set": set(cls_tokens),
                    }
                )
    return windows


def token_inverted_index(windows: list[dict[str, Any]], key: str) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for window_index, window in enumerate(windows):
        for token in window.get(key) or set():
            index[token].append(window_index)
    return dict(index)


def prepare_pages(
    page_corpus: Path,
    trusted_only: bool,
    max_region_lines: int,
    max_region_chars: int,
    min_region_tokens: int,
) -> list[dict[str, Any]]:
    pages = []
    for row in read_jsonl(page_corpus):
        if trusted_only and row.get("page_type") not in spans.TRUSTED_PAGE_TYPES:
            continue
        row = spans.prepare_page(row)
        page_tokens = token_list(spans.page_text(row))
        row["_tokens"] = set(page_tokens)
        row["_class_tokens"] = set(class_tokens(page_tokens))
        row["_region_windows"] = page_region_windows(
            row,
            max_region_lines=max_region_lines,
            max_region_chars=max_region_chars,
            min_region_tokens=min_region_tokens,
        )
        row["_region_token_index"] = token_inverted_index(row["_region_windows"], "token_set")
        row["_region_class_index"] = token_inverted_index(row["_region_windows"], "class_token_set")
        pages.append(row)
    return pages


def normalized_poem_lines(body: str, min_line_tokens: int) -> list[dict[str, Any]]:
    rows = []
    for line_index, raw in enumerate(body.splitlines()):
        tokens = token_list(raw)
        if len(tokens) < min_line_tokens:
            continue
        cls_tokens = class_tokens(tokens)
        rows.append(
            {
                "line_index": line_index,
                "raw": raw.strip(),
                "normalized": spans.normalize(raw),
                "tokens": tokens,
                "class_tokens": cls_tokens,
                "token_set": set(tokens),
                "class_token_set": set(cls_tokens),
            }
        )
    return rows


def longest_common_subsequence(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    for item_a in a:
        current = [0]
        for index_b, item_b in enumerate(b, start=1):
            if item_a == item_b:
                current.append(previous[index_b - 1] + 1)
            else:
                current.append(max(previous[index_b], current[-1]))
        previous = current
    return previous[-1]


def longest_common_contiguous(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for item_a in a:
        current = [0]
        for index_b, item_b in enumerate(b, start=1):
            if item_a == item_b:
                value = previous[index_b - 1] + 1
                current.append(value)
                best = max(best, value)
            else:
                current.append(0)
        previous = current
    return best


def ordered_token_score(line: dict[str, Any], region: dict[str, Any]) -> dict[str, Any]:
    tokens = line["tokens"]
    region_tokens = region["tokens"]
    cls_tokens = line["class_tokens"]
    region_cls_tokens = region["class_tokens"]
    denominator = max(1, len(tokens))

    exact_overlap = len(line["token_set"] & region["token_set"])
    class_overlap = len(line["class_token_set"] & region["class_token_set"])
    overlap_score = (exact_overlap + 0.5 * max(0, class_overlap - exact_overlap)) / max(1, len(line["token_set"]))

    exact_lcs = longest_common_subsequence(tokens, region_tokens)
    class_lcs = longest_common_subsequence(cls_tokens, region_cls_tokens)
    lcs_score = (exact_lcs + 0.5 * max(0, class_lcs - exact_lcs)) / denominator

    exact_contiguous = longest_common_contiguous(tokens, region_tokens)
    class_contiguous = longest_common_contiguous(cls_tokens, region_cls_tokens)
    contiguous_score = (exact_contiguous + 0.5 * max(0, class_contiguous - exact_contiguous)) / denominator

    exact_line = line["normalized"] and line["normalized"] in region["text"]
    score = 1.0 if exact_line else min(1.0, (0.50 * lcs_score) + (0.30 * overlap_score) + (0.20 * contiguous_score))
    return {
        "score": score,
        "kind": "exact" if exact_line else "ordered_token",
        "exact_token_overlap": exact_overlap,
        "class_token_overlap": class_overlap,
        "exact_lcs": exact_lcs,
        "class_lcs": class_lcs,
        "exact_contiguous": exact_contiguous,
        "class_contiguous": class_contiguous,
        "overlap_score": overlap_score,
        "lcs_score": lcs_score,
        "contiguous_score": contiguous_score,
    }


def best_region_match(
    page: dict[str, Any],
    line: dict[str, Any],
    top_windows: int,
    min_prefilter_score: float,
) -> dict[str, Any] | None:
    window_scores: Counter[int] = Counter()
    exact_hits: Counter[int] = Counter()
    for token in line["token_set"]:
        for window_index in (page.get("_region_token_index") or {}).get(token, []):
            window_scores[window_index] += 1.0
            exact_hits[window_index] += 1
    for token in line["class_token_set"]:
        for window_index in (page.get("_region_class_index") or {}).get(token, []):
            window_scores[window_index] += 0.5

    ranked = [
        (score, exact_hits[window_index], window_index)
        for window_index, score in window_scores.items()
        if score >= min_prefilter_score
    ]
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

    best: dict[str, Any] | None = None
    windows = page.get("_region_windows") or []
    for _, _, window_index in ranked[:top_windows]:
        region = windows[window_index]
        score = ordered_token_score(line, region)
        candidate = {
            **score,
            "page_region_source": region["source"],
            "page_line_start": region["line_start"],
            "page_line_end": region["line_end"],
            "region_text": region["text"][:260],
        }
        if best is None or (
            candidate["score"],
            candidate["exact_lcs"],
            candidate["exact_contiguous"],
        ) > (
            best["score"],
            best["exact_lcs"],
            best["exact_contiguous"],
        ):
            best = candidate
    return best


def page_matches(
    page: dict[str, Any],
    line_rows: list[dict[str, Any]],
    min_line_score: float,
    top_windows: int,
    min_prefilter_score: float,
) -> list[dict[str, Any]]:
    matches = []
    for line in line_rows:
        region = best_region_match(page, line, top_windows, min_prefilter_score)
        if region is None:
            continue
        if region["kind"] != "exact" and float(region["score"]) < min_line_score:
            continue
        matches.append(
            {
                "line_index": line["line_index"],
                "kind": region["kind"],
                "score": round(float(region["score"]), 3),
                "exact_lcs": int(region["exact_lcs"]),
                "class_lcs": int(region["class_lcs"]),
                "exact_contiguous": int(region["exact_contiguous"]),
                "class_contiguous": int(region["class_contiguous"]),
                "exact_token_overlap": int(region["exact_token_overlap"]),
                "class_token_overlap": int(region["class_token_overlap"]),
                "page_region_source": region["page_region_source"],
                "page_line_start": region["page_line_start"],
                "page_line_end": region["page_line_end"],
                "region_text": region["region_text"],
                "text": line["raw"],
            }
        )
    return matches


def longest_consecutive_run(values: list[int]) -> int:
    if not values:
        return 0
    best = 1
    current = 1
    previous = values[0]
    for value in values[1:]:
        if value == previous:
            continue
        if value == previous + 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
        previous = value
    return best


def longest_ordered_region_run(group: list[dict[str, Any]]) -> int:
    events = []
    for hit in group:
        scan = int(hit["scan_page"])
        for match in hit["matches"]:
            events.append(
                {
                    "line_index": int(match["line_index"]),
                    "position": (
                        scan,
                        str(match.get("page_region_source") or ""),
                        int(match.get("page_line_start") or 0),
                        int(match.get("page_line_end") or 0),
                    ),
                }
            )
    events.sort(key=lambda item: (item["line_index"], item["position"]))
    best = 0
    for start, event in enumerate(events):
        current = 1
        last_line = event["line_index"]
        last_position = event["position"]
        for next_event in events[start + 1 :]:
            if next_event["line_index"] == last_line:
                continue
            if next_event["line_index"] != last_line + 1:
                break
            if next_event["position"] < last_position:
                continue
            current += 1
            best = max(best, current)
            last_line = next_event["line_index"]
            last_position = next_event["position"]
    return max(best, 1 if events else 0)


def group_adjacent_pages(page_hits: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for hit in sorted(page_hits, key=lambda row: int(row["scan_page"])):
        if not groups or int(hit["scan_page"]) - int(groups[-1][-1]["scan_page"]) > 1:
            groups.append([hit])
        else:
            groups[-1].append(hit)
    return groups


def candidate_windows(page_hits: list[dict[str, Any]], max_span_pages: int) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    for group in group_adjacent_pages(page_hits):
        if len(group) <= max_span_pages:
            windows.append(group)
            continue
        for start in range(len(group)):
            for end in range(start, min(len(group), start + max_span_pages)):
                windows.append(group[start : end + 1])
    return windows


def summarize_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    line_indexes = sorted({index for hit in group for index in hit["line_indexes"]})
    exact_count = sum(hit["exact_line_match_count"] for hit in group)
    ordered_count = sum(hit["ordered_line_match_count"] for hit in group)
    printed_pages = [hit.get("printed_page") for hit in group]
    samples = []
    seen = set()
    for hit in group:
        for match in hit["matches"]:
            key = match["line_index"]
            if key in seen:
                continue
            seen.add(key)
            samples.append(match)
            if len(samples) >= 8:
                break
        if len(samples) >= 8:
            break

    return {
        "candidate_book_id": group[0].get("book_id"),
        "candidate_pdf_file": group[0].get("pdf_file"),
        "scan_page_start": group[0].get("scan_page"),
        "scan_page_end": group[-1].get("scan_page"),
        "printed_page_start": printed_pages[0],
        "printed_page_end": printed_pages[-1],
        "span_page_count": len(group),
        "page_types": sorted({str(hit.get("page_type")) for hit in group}),
        "line_match_count": exact_count + ordered_count,
        "exact_line_match_count": exact_count,
        "ordered_line_match_count": ordered_count,
        "matched_line_indexes": line_indexes,
        "longest_line_run": longest_consecutive_run(line_indexes),
        "longest_ordered_region_run": longest_ordered_region_run(group),
        "score": round(sum(hit["score"] for hit in group), 3),
        "sample_matches": samples,
    }


def candidate_status(candidate: dict[str, Any], source_edition: str | None) -> str:
    has_pages = isinstance(candidate.get("printed_page_start"), int) and isinstance(candidate.get("printed_page_end"), int)
    if not has_pages:
        return "needs_printed_page_sequence"
    exact = int(candidate.get("exact_line_match_count") or 0)
    ordered = int(candidate.get("longest_ordered_region_run") or 0)
    line_count = int(candidate.get("line_match_count") or 0)
    span_count = int(candidate.get("span_page_count") or 0)
    if exact >= 2 and ordered >= 3:
        return "strong_ordered_review"
    if exact >= 1 and ordered >= 3 and line_count >= 5 and span_count <= 3:
        return "manual_ordered_review"
    if source_edition == UNKNOWN_COLLECTION and exact >= 1 and line_count >= 4:
        return "manual_collection_review"
    return "weak_ordered_review"


def page_candidate_pool(
    poem_tokens: set[str],
    poem_class_tokens: set[str],
    allowed_books: set[str],
    pages_by_book: dict[str, list[dict[str, Any]]],
    top_pages: int,
    min_page_score: float,
) -> list[dict[str, Any]]:
    scored = []
    denominator = max(1, len(poem_tokens))
    for book_id in allowed_books:
        for page in pages_by_book.get(book_id, []):
            exact = len(poem_tokens & (page.get("_tokens") or set()))
            cls = len(poem_class_tokens & (page.get("_class_tokens") or set()))
            score = (exact + 0.5 * max(0, cls - exact)) / denominator
            if score >= min_page_score:
                scored.append((score, exact, page))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [{**page, "_page_prefilter_score": round(score, 4)} for score, _, page in scored[:top_pages]]


def markdown_report(rows: list[dict[str, Any]], max_rows: int) -> str:
    lines = [
        "# Ordered Region Audit",
        "",
        "Review-only ordered token matches between remaining poem gaps and local OCR regions.",
        "",
        f"- Rows with candidates: {len(rows)}",
        "",
        "| file | title | current source | status | candidate | lines | sample |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in rows[:max_rows]:
        candidate = row.get("best_candidate") or {}
        start = candidate.get("printed_page_start")
        end = candidate.get("printed_page_end")
        if isinstance(start, int) and isinstance(end, int):
            page_label = str(start) if start == end else f"{start}-{end}"
        else:
            page_label = "পৃষ্ঠা নেই"
        sample_parts = []
        for match in (candidate.get("sample_matches") or [])[:2]:
            page_lines = "-".join(
                str(part)
                for part in [match.get("page_line_start"), match.get("page_line_end")]
                if part is not None
            )
            sample_parts.append(
                f"{match.get('kind')}@{match.get('page_region_source')}:{page_lines}: "
                f"{match.get('text') or ''} => {match.get('region_text') or ''}"
            )
        sample = " / ".join(sample_parts).replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or ""),
                    " ".join(str(part) for part in [row.get("source_edition"), row.get("source_year") or ""] if part),
                    str(row.get("status") or ""),
                    (
                        f"{candidate.get('candidate_book_id')}; {page_label}; "
                        f"scan {candidate.get('scan_page_start')}-{candidate.get('scan_page_end')}"
                    ),
                    (
                        f"{candidate.get('line_match_count', 0)} "
                        f"({candidate.get('exact_line_match_count', 0)} exact, "
                        f"ordered run {candidate.get('longest_ordered_region_run', 0)})"
                    ),
                    sample,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review ordered local-region matches against the OCR page corpus.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.layout.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument("--review-exclusions", default=DEFAULT_REVIEW_EXCLUSIONS)
    parser.add_argument("--output", default="metadata_reports/ordered-region-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/ordered-region-audit.current.md")
    parser.add_argument("--ocr-substitutions", default=None)
    parser.add_argument("--all-books", action="store_true")
    parser.add_argument("--include-logical-aliases", action="store_true")
    parser.add_argument("--include-untrusted-pages", action="store_true")
    parser.add_argument("--max-region-lines", type=int, default=DEFAULT_MAX_REGION_LINES)
    parser.add_argument("--max-region-chars", type=int, default=DEFAULT_MAX_REGION_CHARS)
    parser.add_argument("--min-region-tokens", type=int, default=3)
    parser.add_argument("--min-line-tokens", type=int, default=3)
    parser.add_argument("--min-line-score", type=float, default=0.78)
    parser.add_argument("--region-top-windows", type=int, default=20)
    parser.add_argument("--min-region-prefilter-score", type=float, default=3.0)
    parser.add_argument("--page-top-pages", type=int, default=36)
    parser.add_argument("--min-page-score", type=float, default=0.02)
    parser.add_argument("--max-candidate-span-pages", type=int, default=4)
    parser.add_argument("--min-candidate-lines", type=int, default=3)
    parser.add_argument("--max-rows", type=int, default=120)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    global tqdm
    if args.no_progress:
        tqdm = None

    spans.OCR_SUBSTITUTIONS = spans.load_ocr_substitutions(
        Path(args.ocr_substitutions) if args.ocr_substitutions else None
    )

    pages = prepare_pages(
        Path(args.page_corpus),
        trusted_only=not args.include_untrusted_pages,
        max_region_lines=args.max_region_lines,
        max_region_chars=args.max_region_chars,
        min_region_tokens=args.min_region_tokens,
    )
    pages_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        pages_by_book[str(page.get("book_id") or "")].append(page)

    duplicates = duplicate_ids(Path(args.duplicates_source))
    poems = load_public_gap_poems(Path(args.poems_dir), duplicates)
    review_exclusions = load_review_exclusions(Path(args.review_exclusions))
    rows = []
    poem_iter = poems
    if not args.no_progress:
        poem_iter = progress_iter(
            poems,
            desc="ordered regions",
            unit="poem",
            dynamic_ncols=True,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} poems [{elapsed}<{remaining}, {rate_fmt}]",
        )

    for filename, poem in poem_iter:
        line_rows = normalized_poem_lines(poem.get("body_bn") or "", args.min_line_tokens)
        if not line_rows:
            continue
        poem_tokens = {token for line in line_rows for token in line["token_set"]}
        poem_class_tokens = {token for line in line_rows for token in line["class_token_set"]}
        allowed_books = spans.candidate_books(
            poem,
            all_books=args.all_books,
            include_logical_aliases=args.include_logical_aliases,
        )
        candidate_pages = page_candidate_pool(
            poem_tokens,
            poem_class_tokens,
            allowed_books,
            pages_by_book,
            top_pages=args.page_top_pages,
            min_page_score=args.min_page_score,
        )

        page_hits = []
        for page in candidate_pages:
            matches = page_matches(
                page,
                line_rows,
                min_line_score=args.min_line_score,
                top_windows=args.region_top_windows,
                min_prefilter_score=args.min_region_prefilter_score,
            )
            if len(matches) < args.min_candidate_lines:
                continue
            page_hits.append(
                {
                    "book_id": page.get("book_id"),
                    "pdf_file": page.get("pdf_file"),
                    "scan_page": page.get("scan_page"),
                    "printed_page": page.get("printed_page_fixed"),
                    "page_type": page.get("page_type"),
                    "page_prefilter_score": page.get("_page_prefilter_score"),
                    "matches": matches,
                    "line_indexes": [match["line_index"] for match in matches],
                    "exact_line_match_count": sum(1 for match in matches if match["kind"] == "exact"),
                    "ordered_line_match_count": sum(1 for match in matches if match["kind"] == "ordered_token"),
                    "score": round(sum(float(match["score"]) for match in matches), 3),
                }
            )
        if not page_hits:
            continue

        candidates = [
            summarize_group(group)
            for group in candidate_windows(page_hits, args.max_candidate_span_pages)
            if sum(hit["exact_line_match_count"] + hit["ordered_line_match_count"] for hit in group)
            >= args.min_candidate_lines
        ]
        candidates = [
            candidate
            for candidate in candidates
            if not matches_review_exclusion(filename, candidate, review_exclusions)
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda item: (
                int(item.get("exact_line_match_count") or 0),
                int(item.get("longest_ordered_region_run") or 0),
                int(item.get("line_match_count") or 0),
                float(item.get("score") or 0),
            ),
            reverse=True,
        )
        best = candidates[0]
        rows.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_edition": poem.get("source_edition"),
                "source_year": poem.get("source_year"),
                "source_url": poem.get("source_url"),
                "status": candidate_status(best, poem.get("source_edition")),
                "line_count_considered": len(line_rows),
                "best_candidate": best,
                "top_candidates": candidates[:5],
            }
        )

    rows.sort(
        key=lambda row: (
            row.get("status") != "strong_ordered_review",
            row.get("status") != "manual_ordered_review",
            -int((row.get("best_candidate") or {}).get("exact_line_match_count") or 0),
            -int((row.get("best_candidate") or {}).get("longest_ordered_region_run") or 0),
            -int((row.get("best_candidate") or {}).get("line_match_count") or 0),
            row.get("filename") or "",
        )
    )

    payload = {
        "summary": {
            "poem_count": len(poems),
            "page_count": len(pages),
            "matched_poem_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows).most_common()),
            "max_region_lines": args.max_region_lines,
            "max_region_chars": args.max_region_chars,
            "min_region_tokens": args.min_region_tokens,
            "min_line_tokens": args.min_line_tokens,
            "min_line_score": args.min_line_score,
            "region_top_windows": args.region_top_windows,
            "min_region_prefilter_score": args.min_region_prefilter_score,
            "page_top_pages": args.page_top_pages,
            "min_page_score": args.min_page_score,
            "max_candidate_span_pages": args.max_candidate_span_pages,
            "min_candidate_lines": args.min_candidate_lines,
            "all_books": args.all_books,
            "include_logical_aliases": args.include_logical_aliases,
            "review_exclusions": args.review_exclusions,
            "note": "Review-only report; it does not mutate poem JSON.",
        },
        "matches": rows,
    }
    write_json(Path(args.output), payload)
    markdown = markdown_report(rows, args.max_rows)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
