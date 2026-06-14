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
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for direct python without uv.
    tqdm = None


DEFAULT_REVIEW_EXCLUSIONS = "src/data/metadata-review-exclusions.json"
UNKNOWN_COLLECTION = "সংকলন অজানা"
DEFAULT_MAX_REGION_LINES = 3
DEFAULT_MAX_REGION_CHARS = 520
DEFAULT_REGION_TOP_WINDOWS = 16
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


def compact_length(text: str) -> int:
    return len(compact_text(text))


def profile_text(page: dict[str, Any]) -> str:
    return "\n".join(profile.get("text") or "" for profile in page.get("ocr_profiles") or [])


def page_region_lines(page: dict[str, Any], max_region_chars: int) -> list[str]:
    lines: list[str] = []
    seen = set()
    for source in [page.get("raw_ocr") or "", page.get("raw_pdftotext") or "", profile_text(page)]:
        for raw_line in source.splitlines():
            normalized = spans.normalize(raw_line)
            compact = compact_text(normalized)
            if len(compact) < 3:
                continue
            if compact.isdigit():
                continue
            if len(compact) > max_region_chars:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            lines.append(normalized)
    return lines


def page_region_windows(
    page: dict[str, Any],
    ngram_size: int,
    max_region_lines: int,
    max_region_chars: int,
) -> list[dict[str, Any]]:
    lines = page_region_lines(page, max_region_chars)
    windows = []
    for start in range(len(lines)):
        pieces: list[str] = []
        for end in range(start, min(len(lines), start + max_region_lines)):
            pieces.append(lines[end])
            text = " ".join(pieces)
            if compact_length(text) > max_region_chars:
                break
            exact_grams = char_ngrams(text, ngram_size)
            class_grams = char_ngrams(text, ngram_size, class_mode=True)
            if not exact_grams or not class_grams:
                continue
            windows.append(
                {
                    "line_start": start,
                    "line_end": end,
                    "text": text,
                    "exact_grams": exact_grams,
                    "class_grams": class_grams,
                }
            )
    return windows


def region_inverted_index(windows: list[dict[str, Any]], key: str) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for window_index, window in enumerate(windows):
        for gram in window.get(key) or set():
            index[gram].append(window_index)
    return dict(index)


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
                "exact_gram_set": set(exact_ngrams),
                "class_gram_set": set(class_ngrams),
            }
        )
    return rows


def line_gram_union(line_rows: list[dict[str, Any]], class_mode: bool = False) -> set[str]:
    key = "class_ngrams" if class_mode else "exact_ngrams"
    grams: set[str] = set()
    for line in line_rows:
        grams.update(line.get(key) or [])
    return grams


def representative_line_rows(line_rows: list[dict[str, Any]], max_lines: int) -> list[dict[str, Any]]:
    if max_lines <= 0 or len(line_rows) <= max_lines:
        return line_rows
    if max_lines == 1:
        return [line_rows[0]]
    indexes = {
        round(index * (len(line_rows) - 1) / (max_lines - 1))
        for index in range(max_lines)
    }
    return [line_rows[index] for index in sorted(indexes)]


def parse_int_list(raw: str) -> list[int]:
    values = sorted({int(part) for part in raw.split(",") if part.strip()})
    if not values:
        raise ValueError("At least one n-gram size is required.")
    return values


def feature_index(feature: str, dimensions: int) -> int:
    return zlib.crc32(feature.encode("utf-8")) % dimensions


def iter_embedding_features(
    text: str,
    dimensions: int,
    ngram_sizes: list[int],
    class_mode: bool,
    base_weight: float,
):
    min_size = min(ngram_sizes)
    prefix = "class:" if class_mode else "exact:"
    for size in ngram_sizes:
        size_weight = size / min_size
        weight = base_weight * size_weight
        for gram in set(ngram_sequence(text, size, class_mode=class_mode)):
            yield feature_index(f"{prefix}{size}:{gram}", dimensions), weight


def sparse_embedding_features(text: str, dimensions: int, ngram_sizes: list[int]) -> dict[int, float]:
    features: Counter[int] = Counter()
    for index, weight in iter_embedding_features(
        text,
        dimensions,
        ngram_sizes,
        class_mode=False,
        base_weight=1.0,
    ):
        features[index] += weight
    for index, weight in iter_embedding_features(
        text,
        dimensions,
        ngram_sizes,
        class_mode=True,
        base_weight=0.5,
    ):
        features[index] += weight
    return dict(features)


def sparse_embedding_norm(features: dict[int, float]) -> float:
    return sum(weight * weight for weight in features.values()) ** 0.5


def attach_region_embeddings(
    windows: list[dict[str, Any]],
    dimensions: int,
    ngram_sizes: list[int],
) -> None:
    for window in windows:
        features = sparse_embedding_features(window["text"], dimensions, ngram_sizes)
        window["embedding_features"] = features
        window["embedding_norm"] = sparse_embedding_norm(features)


def region_embedding_index(windows: list[dict[str, Any]]) -> dict[int, list[tuple[int, float]]]:
    index: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for window_index, window in enumerate(windows):
        for feature, weight in (window.get("embedding_features") or {}).items():
            index[int(feature)].append((window_index, float(weight)))
    return dict(index)


def prepare_pages(
    page_corpus: Path,
    trusted_only: bool,
    ngram_size: int,
    embedding_dimensions: int,
    embedding_ngram_sizes: list[int],
    use_vector_prefilter: bool,
    max_region_lines: int,
    max_region_chars: int,
) -> list[dict[str, Any]]:
    rows = []
    for row in read_jsonl(page_corpus):
        if trusted_only and row.get("page_type") not in spans.TRUSTED_PAGE_TYPES:
            continue
        row = spans.prepare_page(row)
        row["_char_ngrams"] = char_ngrams(spans.page_text(row), ngram_size)
        row["_class_ngrams"] = char_ngrams(spans.page_text(row), ngram_size, class_mode=True)
        row["_region_windows"] = page_region_windows(
            row,
            ngram_size,
            max_region_lines=max_region_lines,
            max_region_chars=max_region_chars,
        )
        row["_region_exact_index"] = region_inverted_index(row["_region_windows"], "exact_grams")
        row["_region_class_index"] = region_inverted_index(row["_region_windows"], "class_grams")
        rows.append(row)
    return rows


def ensure_region_embedding_index(
    page: dict[str, Any],
    embedding_dimensions: int,
    embedding_ngram_sizes: list[int],
) -> None:
    if page.get("_region_embedding_index") is not None:
        return
    attach_region_embeddings(page.get("_region_windows") or [], embedding_dimensions, embedding_ngram_sizes)
    page["_region_embedding_index"] = region_embedding_index(page.get("_region_windows") or [])


def page_candidate_pool(
    line_rows: list[dict[str, Any]],
    allowed_books: set[str],
    pages_by_book: dict[str, list[dict[str, Any]]],
    ngram_size: int,
    embedding_dimensions: int,
    embedding_ngram_sizes: list[int],
    vector_top_pages: int,
    vector_page_prefilter_multiplier: int,
    min_vector_score: float,
    min_candidate_lines: int,
    vector_top_regions_per_line: int,
    vector_max_lines: int,
    use_vector_prefilter: bool,
) -> list[dict[str, Any]]:
    allowed_pages = [
        page
        for book_id in allowed_books
        for page in pages_by_book.get(book_id, [])
    ]
    if not use_vector_prefilter:
        return [{**page, "_vector_score": None} for page in allowed_pages]

    poem_exact_grams = line_gram_union(line_rows)
    poem_class_grams = line_gram_union(line_rows, class_mode=True)
    minimum_page_prefilter = min_candidate_lines * ngram_size
    page_prefiltered = []
    for page in allowed_pages:
        exact_prefilter = len(poem_exact_grams & (page.get("_char_ngrams") or set()))
        class_prefilter = len(poem_class_grams & (page.get("_class_ngrams") or set()))
        weighted_prefilter = exact_prefilter + (0.5 * max(0, class_prefilter - exact_prefilter))
        if exact_prefilter + class_prefilter < minimum_page_prefilter:
            continue
        page_prefiltered.append((weighted_prefilter, exact_prefilter, class_prefilter, page))
    if not page_prefiltered:
        return []
    page_prefiltered.sort(key=lambda item: item[:3], reverse=True)
    page_prefilter_limit = max(vector_top_pages, vector_top_pages * max(1, vector_page_prefilter_multiplier))
    allowed_pages = [page for *_scores, page in page_prefiltered[:page_prefilter_limit]]

    vector_lines = representative_line_rows(line_rows, vector_max_lines)
    line_embeddings = []
    for line in vector_lines:
        features = sparse_embedding_features(line["normalized"], embedding_dimensions, embedding_ngram_sizes)
        norm = sparse_embedding_norm(features)
        if norm > 0:
            line_embeddings.append({**line, "embedding_features": features, "embedding_norm": norm})

    if not line_embeddings:
        return [{**page, "_vector_score": None} for page in allowed_pages]

    scored_pages = []
    required_hits = min(min_candidate_lines, len(line_embeddings))
    for page in allowed_pages:
        ensure_region_embedding_index(page, embedding_dimensions, embedding_ngram_sizes)
        region_index = page.get("_region_embedding_index") or {}
        windows = page.get("_region_windows") or []
        if not region_index or not windows:
            continue
        line_hits = []
        for line in line_embeddings:
            region_scores: Counter[int] = Counter()
            for feature, line_weight in line["embedding_features"].items():
                for window_index, window_weight in region_index.get(feature, []):
                    region_scores[window_index] += float(line_weight) * float(window_weight)
            ranked_regions = []
            for window_index, dot_product in region_scores.items():
                window = windows[window_index]
                window_norm = float(window.get("embedding_norm") or 0)
                if window_norm <= 0:
                    continue
                score = float(dot_product) / (float(line["embedding_norm"]) * window_norm)
                if score >= min_vector_score:
                    ranked_regions.append((score, window_index, window))
            if not ranked_regions:
                continue
            ranked_regions.sort(
                key=lambda item: (
                    item[0],
                    -int(item[2].get("line_end", 0)) + int(item[2].get("line_start", 0)),
                ),
                reverse=True,
            )
            best_score, best_window_index, best_window = ranked_regions[:vector_top_regions_per_line][0]
            line_hits.append(
                {
                    "line_index": int(line["line_index"]),
                    "score": best_score,
                    "window_index": int(best_window_index),
                    "page_line_start": int(best_window.get("line_start") or 0),
                    "page_line_end": int(best_window.get("line_end") or 0),
                }
            )
        if len(line_hits) < required_hits:
            continue
        line_indexes = sorted({hit["line_index"] for hit in line_hits})
        hit_scores = sorted((float(hit["score"]) for hit in line_hits), reverse=True)
        ordered_region_run = longest_ordered_vector_region_run(line_hits)
        line_run = longest_consecutive_run(line_indexes)
        page_score = sum(hit_scores[:required_hits]) / max(1, required_hits)
        scored_pages.append(
            (
                ordered_region_run,
                line_run,
                len(line_hits),
                page_score,
                {
                    **page,
                    "_vector_score": round(page_score, 4),
                    "_vector_line_hits": len(line_hits),
                    "_vector_longest_line_run": line_run,
                    "_vector_ordered_region_run": ordered_region_run,
                },
            )
        )
    if not scored_pages:
        return [{**page, "_vector_score": None} for page in allowed_pages]

    selected = []
    for *_, page in sorted(scored_pages, key=lambda item: item[:4], reverse=True)[:vector_top_pages]:
        selected.append(page)
    return selected


def best_region_match(page: dict[str, Any], line: dict[str, Any], top_windows: int) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    line_exact_set = line.get("exact_gram_set") or set(line.get("exact_ngrams") or [])
    line_class_set = line.get("class_gram_set") or set(line.get("class_ngrams") or [])
    minimum_prefilter = max(3.0, min(len(line_exact_set), len(line_class_set)) * 0.2)
    window_scores: Counter[int] = Counter()
    exact_hits: Counter[int] = Counter()
    exact_index = page.get("_region_exact_index") or {}
    class_index = page.get("_region_class_index") or {}
    for gram in line_exact_set:
        for window_index in exact_index.get(gram, []):
            window_scores[window_index] += 1.0
            exact_hits[window_index] += 1
    for gram in line_class_set:
        for window_index in class_index.get(gram, []):
            window_scores[window_index] += 0.5

    ranked_regions = [
        (score, exact_hits[window_index], window_index)
        for window_index, score in window_scores.items()
        if score >= minimum_prefilter
    ]
    ranked_regions.sort(key=lambda item: (item[0], item[1]), reverse=True)
    windows = page.get("_region_windows") or []
    for _, _, window_index in ranked_regions[:top_windows]:
        region = windows[window_index]
        exact = line["normalized"] in region["text"]
        fuzzy_score = fuzzy_ngram_score(
            line.get("exact_ngrams") or [],
            line.get("class_ngrams") or [],
            region.get("exact_grams") or set(),
            region.get("class_grams") or set(),
        )
        if fuzzy_score is None:
            continue
        score = 1.0 if exact else float(fuzzy_score["score"])
        candidate = {
            "kind": "exact" if exact else "fuzzy_char",
            "score": score,
            "exact_score": fuzzy_score["exact_score"],
            "class_score": fuzzy_score["class_score"],
            "class_only_score": fuzzy_score["class_only_score"],
            "contiguous_bonus": fuzzy_score["contiguous_bonus"],
            "page_line_start": region["line_start"],
            "page_line_end": region["line_end"],
            "region_text": region["text"][:220],
        }
        if best is None or (
            candidate["score"],
            candidate["exact_score"],
            -int(candidate["page_line_end"] - candidate["page_line_start"]),
        ) > (
            best["score"],
            best["exact_score"],
            -int(best["page_line_end"] - best["page_line_start"]),
        ):
            best = candidate
    return best


def page_match(
    page: dict[str, Any],
    line_rows: list[dict[str, Any]],
    ngram_size: int,
    min_line_score: float,
    region_top_windows: int,
) -> list[dict[str, Any]]:
    matches = []
    for line in line_rows:
        region_match = best_region_match(page, line, region_top_windows)
        if region_match is None:
            continue
        score = float(region_match["score"])
        if region_match["kind"] == "exact" or score >= min_line_score:
            matches.append(
                {
                    "line_index": line["line_index"],
                    "kind": region_match["kind"],
                    "score": round(score, 3),
                    "exact_score": round(float(region_match["exact_score"]), 3),
                    "class_score": round(float(region_match["class_score"]), 3),
                    "class_only_score": round(float(region_match["class_only_score"]), 3),
                    "contiguous_bonus": round(float(region_match["contiguous_bonus"]), 3),
                    "page_line_start": region_match["page_line_start"],
                    "page_line_end": region_match["page_line_end"],
                    "region_text": region_match["region_text"],
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


def longest_ordered_vector_region_run(line_hits: list[dict[str, Any]]) -> int:
    events = []
    for hit in line_hits:
        events.append(
            {
                "line_index": int(hit["line_index"]),
                "position": (
                    int(hit.get("page_line_start") or 0),
                    int(hit.get("page_line_end") or 0),
                    int(hit.get("window_index") or 0),
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
                    "page_line_start": match.get("page_line_start"),
                    "page_line_end": match.get("page_line_end"),
                    "region_text": match.get("region_text"),
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
    longest = int(candidate.get("longest_line_run") or 0)
    exact = int(candidate.get("exact_line_match_count") or 0)
    line_count = int(candidate.get("line_match_count") or 0)
    span_count = int(candidate.get("span_page_count") or 0)
    if longest < 3:
        return "weak_fuzzy_review"
    if exact >= 2:
        return "strong_fuzzy_review"
    if exact >= 1 and line_count >= 6 and longest >= 4 and span_count <= 3:
        return "strong_fuzzy_review"
    if source_edition == UNKNOWN_COLLECTION and line_count >= 4:
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
        sample_parts = []
        for match in (candidate.get("sample_matches") or [])[:2]:
            region = match.get("region_text") or ""
            page_lines = "-".join(
                str(part)
                for part in [match.get("page_line_start"), match.get("page_line_end")]
                if part is not None
            )
            sample_parts.append(
                f"{match.get('kind')}@{page_lines}: {match.get('text') or ''} => {region}"
            )
        sample = " / ".join(sample_parts)
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
    parser.add_argument("--vector-top-pages", type=int, default=8)
    parser.add_argument("--vector-page-prefilter-multiplier", type=int, default=2)
    parser.add_argument("--vector-top-regions-per-line", type=int, default=2)
    parser.add_argument("--vector-max-lines", type=int, default=6)
    parser.add_argument("--min-vector-score", type=float, default=0.03)
    parser.add_argument("--max-candidate-span-pages", type=int, default=4)
    parser.add_argument("--max-region-lines", type=int, default=DEFAULT_MAX_REGION_LINES)
    parser.add_argument("--max-region-chars", type=int, default=DEFAULT_MAX_REGION_CHARS)
    parser.add_argument("--region-top-windows", type=int, default=DEFAULT_REGION_TOP_WINDOWS)
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
        max_region_lines=args.max_region_lines,
        max_region_chars=args.max_region_chars,
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
            args.ngram_size,
            args.embedding_dimensions,
            embedding_ngram_sizes,
            args.vector_top_pages,
            args.vector_page_prefilter_multiplier,
            args.min_vector_score,
            args.min_candidate_lines,
            args.vector_top_regions_per_line,
            args.vector_max_lines,
            use_vector_prefilter,
        )
        for page in candidate_pages:
            exact_prefilter = len(poem_exact_grams & (page.get("_char_ngrams") or set()))
            class_prefilter = len(poem_class_grams & (page.get("_class_ngrams") or set()))
            if exact_prefilter + class_prefilter < args.min_candidate_lines * args.ngram_size:
                continue
            matches = page_match(
                page,
                line_rows,
                args.ngram_size,
                args.min_line_score,
                args.region_top_windows,
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
                    "vector_score": page.get("_vector_score"),
                    "vector_line_hits": page.get("_vector_line_hits"),
                    "vector_longest_line_run": page.get("_vector_longest_line_run"),
                    "vector_ordered_region_run": page.get("_vector_ordered_region_run"),
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
            "vector_prefilter": use_vector_prefilter,
            "embedding_dimensions": args.embedding_dimensions,
            "embedding_ngram_sizes": embedding_ngram_sizes,
            "vector_top_pages": args.vector_top_pages,
            "vector_page_prefilter_multiplier": args.vector_page_prefilter_multiplier,
            "vector_top_regions_per_line": args.vector_top_regions_per_line,
            "vector_max_lines": args.vector_max_lines,
            "min_vector_score": args.min_vector_score,
            "vector_scope": "contiguous_ocr_region_windows",
            "page_wide_prefilter_is_evidence": False,
            "embedding_feature_channels": [
                "exact_contiguous_character_shingles",
                "ocr_class_normalized_contiguous_character_shingles",
            ],
            "max_candidate_span_pages": args.max_candidate_span_pages,
            "max_region_lines": args.max_region_lines,
            "max_region_chars": args.max_region_chars,
            "region_top_windows": args.region_top_windows,
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
