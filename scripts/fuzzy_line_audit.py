#!/usr/bin/env python3
"""Review fuzzy line-level poem/page matches against the OCR page corpus.

This is a diagnostic step only. It writes sidecar reports and never mutates
poem JSON. It is intended for remaining metadata gaps where exact line anchors
and phrase-window matching have already failed.
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import propose_poem_spans as spans

try:
    import numpy as np
except ImportError:  # pragma: no cover - script can still use the Python fallback.
    np = None

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for direct python without uv.
    tqdm = None


DEFAULT_REVIEW_EXCLUSIONS = "src/data/metadata-review-exclusions.json"
UNKNOWN_COLLECTION = "সংকলন অজানা"
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
        expected_start = item.get("candidate_page_start")
        expected_end = item.get("candidate_page_end")
        if expected_start == candidate.get("printed_page_start") and expected_end == candidate.get("printed_page_end"):
            return True
    return False


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


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", spans.normalize(text))


def class_signature(text: str) -> str:
    return "".join(CHAR_CLASS_MAP.get(char, char) for char in text)


def ngram_sequence(text: str, size: int, class_mode: bool = False) -> list[str]:
    compact = compact_text(text)
    if class_mode:
        compact = class_signature(compact)
    if len(compact) < size:
        return []
    return [compact[index : index + size] for index in range(len(compact) - size + 1)]


def char_ngrams(text: str, size: int, class_mode: bool = False) -> set[str]:
    return set(ngram_sequence(text, size, class_mode=class_mode))


def longest_run(flags: list[bool]) -> int:
    best = 0
    current = 0
    for flag in flags:
        if flag:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def fuzzy_ngram_score(
    exact_sequence: list[str],
    class_sequence: list[str],
    page_exact_grams: set[str],
    page_class_grams: set[str],
) -> dict[str, Any] | None:
    if not exact_sequence or not class_sequence:
        return None

    exact_flags = [gram in page_exact_grams for gram in exact_sequence]
    class_flags = [gram in page_class_grams for gram in class_sequence]
    exact_score = sum(exact_flags) / len(exact_sequence)
    class_score = sum(class_flags) / len(class_sequence)
    class_only_score = max(0.0, class_score - exact_score)

    exact_run_ratio = longest_run(exact_flags) / len(exact_sequence)
    class_run_ratio = longest_run(class_flags) / len(class_sequence)
    contiguous_bonus = min(0.18, 0.18 * max(exact_run_ratio, 0.5 * class_run_ratio))
    score = min(1.0, exact_score + (0.5 * class_only_score) + contiguous_bonus)

    return {
        "score": score,
        "exact_score": exact_score,
        "class_score": class_score,
        "class_only_score": class_only_score,
        "contiguous_bonus": contiguous_bonus,
        "exact_run_ratio": exact_run_ratio,
        "class_run_ratio": class_run_ratio,
    }


def normalized_lines(body: str, min_chars: int, ngram_size: int) -> list[dict[str, Any]]:
    rows = []
    for line_index, raw in enumerate(body.splitlines()):
        normalized = spans.normalize(raw)
        compact = compact_text(raw)
        if len(compact) < min_chars:
            continue
        exact_ngrams = ngram_sequence(normalized, ngram_size)
        class_ngrams = ngram_sequence(normalized, ngram_size, class_mode=True)
        if not exact_ngrams or not class_ngrams:
            continue
        rows.append(
            {
                "line_index": line_index,
                "raw": raw.strip(),
                "normalized": normalized,
                "compact": compact,
                "exact_ngrams": exact_ngrams,
                "class_ngrams": class_ngrams,
            }
        )
    return rows


def line_gram_union(line_rows: list[dict[str, Any]], class_mode: bool = False) -> set[str]:
    key = "class_ngrams" if class_mode else "exact_ngrams"
    grams: set[str] = set()
    for line in line_rows:
        grams.update(line.get(key) or [])
    return grams


def parse_int_list(raw: str) -> list[int]:
    values = sorted({int(part) for part in raw.split(",") if part.strip()})
    if not values:
        raise ValueError("At least one n-gram size is required.")
    return values


def feature_index(feature: str, dimensions: int) -> int:
    return zlib.crc32(feature.encode("utf-8")) % dimensions


def add_embedding_features(
    vector: Any,
    text: str,
    dimensions: int,
    ngram_sizes: list[int],
    class_mode: bool,
    base_weight: float,
) -> None:
    min_size = min(ngram_sizes)
    prefix = "class:" if class_mode else "exact:"
    for size in ngram_sizes:
        size_weight = size / min_size
        weight = base_weight * size_weight
        for gram in set(ngram_sequence(text, size, class_mode=class_mode)):
            vector[feature_index(f"{prefix}{size}:{gram}", dimensions)] += weight


def text_embedding(text: str, dimensions: int, ngram_sizes: list[int]) -> Any:
    if np is None:
        return None
    vector = np.zeros(dimensions, dtype=np.float32)
    add_embedding_features(vector, text, dimensions, ngram_sizes, class_mode=False, base_weight=1.0)
    add_embedding_features(vector, text, dimensions, ngram_sizes, class_mode=True, base_weight=0.5)
    return vector


def embedding_norm(vector: Any) -> float:
    if np is None or vector is None:
        return 0.0
    return float(np.linalg.norm(vector))


def prepare_pages(
    page_corpus: Path,
    trusted_only: bool,
    ngram_size: int,
    embedding_dimensions: int,
    embedding_ngram_sizes: list[int],
    use_vector_prefilter: bool,
) -> list[dict[str, Any]]:
    rows = []
    for row in read_jsonl(page_corpus):
        if trusted_only and row.get("page_type") not in spans.TRUSTED_PAGE_TYPES:
            continue
        row = spans.prepare_page(row)
        row["_char_ngrams"] = char_ngrams(spans.page_text(row), ngram_size)
        row["_class_ngrams"] = char_ngrams(spans.page_text(row), ngram_size, class_mode=True)
        if use_vector_prefilter and np is not None:
            vector = text_embedding(spans.page_text(row), embedding_dimensions, embedding_ngram_sizes)
            row["_embedding"] = vector
            row["_embedding_norm"] = embedding_norm(vector)
        rows.append(row)
    return rows


def build_embedding_indexes(pages_by_book: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    if np is None:
        return {}
    indexes: dict[str, dict[str, Any]] = {}
    for book_id, pages in pages_by_book.items():
        rows = [
            (page, page.get("_embedding"), float(page.get("_embedding_norm") or 0))
            for page in pages
            if page.get("_embedding") is not None and float(page.get("_embedding_norm") or 0) > 0
        ]
        if not rows:
            continue
        indexes[book_id] = {
            "pages": [row[0] for row in rows],
            "matrix": np.vstack([row[1] for row in rows]),
            "norms": np.array([row[2] for row in rows], dtype=np.float32),
        }
    return indexes


def page_candidate_pool(
    line_rows: list[dict[str, Any]],
    allowed_books: set[str],
    pages_by_book: dict[str, list[dict[str, Any]]],
    embedding_indexes: dict[str, dict[str, Any]],
    embedding_dimensions: int,
    embedding_ngram_sizes: list[int],
    vector_top_pages: int,
    min_vector_score: float,
    use_vector_prefilter: bool,
) -> list[dict[str, Any]]:
    allowed_pages = [
        page
        for book_id in allowed_books
        for page in pages_by_book.get(book_id, [])
    ]
    if not use_vector_prefilter or np is None:
        return [{**page, "_vector_score": None} for page in allowed_pages]
    poem_text = "\n".join(line["raw"] for line in line_rows)
    poem_vector = text_embedding(poem_text, embedding_dimensions, embedding_ngram_sizes)
    poem_norm = embedding_norm(poem_vector)
    if poem_vector is None or poem_norm == 0:
        return [{**page, "_vector_score": None} for page in allowed_pages]

    scored_pages = []
    for book_id in allowed_books:
        index = embedding_indexes.get(book_id)
        if not index:
            continue
        scores = (index["matrix"] @ poem_vector) / (index["norms"] * poem_norm)
        order = np.argsort(scores)[::-1]
        for offset in order[:vector_top_pages]:
            score = float(scores[int(offset)])
            if score < min_vector_score:
                continue
            scored_pages.append((score, index["pages"][int(offset)]))
    if not scored_pages:
        return [{**page, "_vector_score": None} for page in allowed_pages]

    selected = []
    for score, page in sorted(scored_pages, key=lambda item: item[0], reverse=True)[:vector_top_pages]:
        selected.append({**page, "_vector_score": round(score, 4)})
    return selected


def page_match(
    page: dict[str, Any],
    line_rows: list[dict[str, Any]],
    ngram_size: int,
    min_line_score: float,
) -> list[dict[str, Any]]:
    page_text = spans.page_text(page)
    page_grams = page.get("_char_ngrams") or set()
    page_class_grams = page.get("_class_ngrams") or set()
    matches = []
    for line in line_rows:
        normalized = line["normalized"]
        exact = normalized in page_text
        fuzzy_score = fuzzy_ngram_score(
            line.get("exact_ngrams") or [],
            line.get("class_ngrams") or [],
            page_grams,
            page_class_grams,
        )
        if fuzzy_score is None:
            continue
        score = 1.0 if exact else float(fuzzy_score["score"])
        if exact or score >= min_line_score:
            matches.append(
                {
                    "line_index": line["line_index"],
                    "kind": "exact" if exact else "fuzzy_char",
                    "score": round(score, 3),
                    "exact_score": round(float(fuzzy_score["exact_score"]), 3),
                    "class_score": round(float(fuzzy_score["class_score"]), 3),
                    "class_only_score": round(float(fuzzy_score["class_only_score"]), 3),
                    "contiguous_bonus": round(float(fuzzy_score["contiguous_bonus"]), 3),
                    "text": line["raw"],
                }
            )
    return matches


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


def summarize_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    line_indexes = sorted({index for hit in group for index in hit["line_indexes"]})
    exact_count = sum(hit["exact_line_match_count"] for hit in group)
    fuzzy_count = sum(hit["fuzzy_line_match_count"] for hit in group)
    printed_pages = [hit.get("printed_page") for hit in group]
    start_page = printed_pages[0]
    end_page = printed_pages[-1]
    samples = []
    seen = set()
    for hit in group:
        for match in hit["matches"]:
            key = match["line_index"]
            if key in seen:
                continue
            seen.add(key)
            samples.append(
                {
                    "line_index": match["line_index"],
                    "kind": match["kind"],
                    "score": match["score"],
                    "exact_score": match.get("exact_score"),
                    "class_score": match.get("class_score"),
                    "class_only_score": match.get("class_only_score"),
                    "contiguous_bonus": match.get("contiguous_bonus"),
                    "text": match["text"],
                }
            )
            if len(samples) >= 8:
                break
        if len(samples) >= 8:
            break

    return {
        "candidate_book_id": group[0].get("book_id"),
        "candidate_pdf_file": group[0].get("pdf_file"),
        "scan_page_start": group[0].get("scan_page"),
        "scan_page_end": group[-1].get("scan_page"),
        "printed_page_start": start_page,
        "printed_page_end": end_page,
        "span_page_count": len(group),
        "page_types": sorted({str(hit.get("page_type")) for hit in group}),
        "line_match_count": exact_count + fuzzy_count,
        "exact_line_match_count": exact_count,
        "fuzzy_line_match_count": fuzzy_count,
        "matched_line_indexes": line_indexes,
        "longest_line_run": longest_consecutive_run(line_indexes),
        "score": round(sum(hit["score"] for hit in group), 3),
        "sample_matches": samples,
    }


def candidate_status(candidate: dict[str, Any], source_edition: str | None) -> str:
    has_pages = isinstance(candidate.get("printed_page_start"), int) and isinstance(candidate.get("printed_page_end"), int)
    if not has_pages:
        return "needs_printed_page_sequence"
    if int(candidate.get("longest_line_run") or 0) < 3:
        return "weak_fuzzy_review"
    if int(candidate.get("exact_line_match_count") or 0) >= 2:
        return "strong_fuzzy_review"
    if int(candidate.get("line_match_count") or 0) >= 6 and int(candidate.get("span_page_count") or 0) <= 3:
        return "strong_fuzzy_review"
    if source_edition == UNKNOWN_COLLECTION and int(candidate.get("line_match_count") or 0) >= 4:
        return "manual_collection_review"
    return "weak_fuzzy_review"


def markdown_report(rows: list[dict[str, Any]], max_rows: int) -> str:
    lines = [
        "# Fuzzy Line Audit",
        "",
        "Review-only character-ngram line matches between remaining poem gaps and OCR pages.",
        "",
        f"- Rows with candidates: {len(rows)}",
        "",
        "| file | title | current source | status | candidate | lines | sample |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in rows[:max_rows]:
        candidate = row.get("best_candidate") or {}
        page_start = candidate.get("printed_page_start")
        page_end = candidate.get("printed_page_end")
        if isinstance(page_start, int) and isinstance(page_end, int):
            page_label = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
        else:
            page_label = "পৃষ্ঠা নেই"
        page_summary = f"{candidate.get('candidate_book_id')}; {page_label}; scan {candidate.get('scan_page_start')}-{candidate.get('scan_page_end')}"
        sample = " / ".join(match.get("text") or "" for match in (candidate.get("sample_matches") or [])[:2])
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or ""),
                    " ".join(str(part) for part in [row.get("source_edition"), row.get("source_year") or ""] if part),
                    str(row.get("status") or ""),
                    page_summary,
                    (
                        f"{candidate.get('line_match_count', 0)} "
                        f"({candidate.get('exact_line_match_count', 0)} exact, "
                        f"run {candidate.get('longest_line_run', 0)})"
                    ),
                    sample.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review fuzzy line matches against the OCR page corpus.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.layout.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument("--review-exclusions", default=DEFAULT_REVIEW_EXCLUSIONS)
    parser.add_argument("--output", default="metadata_reports/fuzzy-line-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/fuzzy-line-audit.current.md")
    parser.add_argument("--ocr-substitutions", default=None)
    parser.add_argument("--all-books", action="store_true")
    parser.add_argument("--include-logical-aliases", action="store_true")
    parser.add_argument("--include-untrusted-pages", action="store_true")
    parser.add_argument("--ngram-size", type=int, default=3)
    parser.add_argument("--no-vector-prefilter", action="store_true")
    parser.add_argument("--embedding-dimensions", type=int, default=32768)
    parser.add_argument(
        "--embedding-ngram-sizes",
        default="3,5,8",
        help="Comma-separated contiguous shingle sizes used by the vector prefilter.",
    )
    parser.add_argument("--vector-top-pages", type=int, default=48)
    parser.add_argument("--min-vector-score", type=float, default=0.03)
    parser.add_argument("--max-candidate-span-pages", type=int, default=4)
    parser.add_argument("--min-line-chars", type=int, default=20)
    parser.add_argument("--min-line-score", type=float, default=0.62)
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
    embedding_ngram_sizes = parse_int_list(args.embedding_ngram_sizes)
    use_vector_prefilter = not args.no_vector_prefilter

    pages = prepare_pages(
        Path(args.page_corpus),
        trusted_only=not args.include_untrusted_pages,
        ngram_size=args.ngram_size,
        embedding_dimensions=args.embedding_dimensions,
        embedding_ngram_sizes=embedding_ngram_sizes,
        use_vector_prefilter=use_vector_prefilter,
    )
    pages_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        pages_by_book[str(page.get("book_id") or "")].append(page)
    embedding_indexes = build_embedding_indexes(pages_by_book) if use_vector_prefilter else {}

    duplicates = duplicate_ids(Path(args.duplicates_source))
    poems = load_public_gap_poems(Path(args.poems_dir), duplicates)
    review_exclusions = load_review_exclusions(Path(args.review_exclusions))
    rows = []
    poem_iter = poems
    if not args.no_progress:
        poem_iter = progress_iter(
            poems,
            desc="fuzzy lines",
            unit="poem",
            dynamic_ncols=True,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} poems [{elapsed}<{remaining}, {rate_fmt}]",
        )

    for filename, poem in poem_iter:
        line_rows = normalized_lines(poem.get("body_bn") or "", args.min_line_chars, args.ngram_size)
        if not line_rows:
            continue
        poem_exact_grams = line_gram_union(line_rows)
        poem_class_grams = line_gram_union(line_rows, class_mode=True)
        allowed_books = spans.candidate_books(
            poem,
            all_books=args.all_books,
            include_logical_aliases=args.include_logical_aliases,
        )
        page_hits = []
        candidate_pages = page_candidate_pool(
            line_rows,
            allowed_books,
            pages_by_book,
            embedding_indexes,
            args.embedding_dimensions,
            embedding_ngram_sizes,
            args.vector_top_pages,
            args.min_vector_score,
            use_vector_prefilter,
        )
        for page in candidate_pages:
            exact_prefilter = len(poem_exact_grams & (page.get("_char_ngrams") or set()))
            class_prefilter = len(poem_class_grams & (page.get("_class_ngrams") or set()))
            if exact_prefilter + class_prefilter < args.min_candidate_lines * args.ngram_size:
                continue
            matches = page_match(page, line_rows, args.ngram_size, args.min_line_score)
            if len(matches) < args.min_candidate_lines:
                continue
            page_hits.append(
                {
                    "book_id": page.get("book_id"),
                    "pdf_file": page.get("pdf_file"),
                    "scan_page": page.get("scan_page"),
                    "printed_page": page.get("printed_page_fixed"),
                    "page_type": page.get("page_type"),
                    "vector_score": page.get("_vector_score"),
                    "matches": matches,
                    "line_indexes": [match["line_index"] for match in matches],
                    "exact_line_match_count": sum(1 for match in matches if match["kind"] == "exact"),
                    "fuzzy_line_match_count": sum(1 for match in matches if match["kind"] == "fuzzy_char"),
                    "score": round(sum(float(match["score"]) for match in matches), 3),
                }
            )
        if not page_hits:
            continue

        candidates = [
            summarize_group(group)
            for group in candidate_windows(page_hits, args.max_candidate_span_pages)
            if sum(hit["exact_line_match_count"] + hit["fuzzy_line_match_count"] for hit in group) >= args.min_candidate_lines
        ]
        candidates.sort(
            key=lambda item: (
                int(item.get("longest_line_run") or 0),
                int(item.get("line_match_count") or 0),
                int(item.get("exact_line_match_count") or 0),
                float(item.get("score") or 0),
            ),
            reverse=True,
        )
        candidates = [
            candidate
            for candidate in candidates
            if not matches_review_exclusion(filename, candidate, review_exclusions)
        ]
        if not candidates:
            continue

        best = candidates[0]
        status = candidate_status(best, poem.get("source_edition"))
        rows.append(
            {
                "filename": filename,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "source_edition": poem.get("source_edition"),
                "source_year": poem.get("source_year"),
                "source_url": poem.get("source_url"),
                "status": status,
                "line_count_considered": len(line_rows),
                "best_candidate": best,
                "top_candidates": candidates[:5],
            }
        )

    rows.sort(
        key=lambda row: (
            row.get("status") != "strong_fuzzy_review",
            row.get("status") != "manual_collection_review",
            -int((row.get("best_candidate") or {}).get("line_match_count") or 0),
            -int((row.get("best_candidate") or {}).get("exact_line_match_count") or 0),
            row.get("filename") or "",
        )
    )
    payload = {
        "summary": {
            "poem_count": len(poems),
            "page_count": len(pages),
            "matched_poem_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows).most_common()),
            "ngram_size": args.ngram_size,
            "vector_prefilter": use_vector_prefilter and np is not None,
            "embedding_dimensions": args.embedding_dimensions,
            "embedding_ngram_sizes": embedding_ngram_sizes,
            "vector_top_pages": args.vector_top_pages,
            "min_vector_score": args.min_vector_score,
            "max_candidate_span_pages": args.max_candidate_span_pages,
            "min_line_chars": args.min_line_chars,
            "min_line_score": args.min_line_score,
            "class_match_weight": 0.5,
            "max_contiguous_bonus": 0.18,
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
