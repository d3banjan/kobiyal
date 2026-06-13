#!/usr/bin/env python3
"""Audit OCR tokens against the current poem-text lexicon.

This is a diagnostic step only. It writes sidecar reports and never mutates
poem JSON or page-corpus rows.
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

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for direct python without uv.
    tqdm = None

BANGLA_TO_ASCII = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
WORD_RE = re.compile(r"[\u0980-\u09FF]+")
DIGIT_RE = re.compile(r"^[\u09E6-\u09EF0-9]+$")

OCR_EQUIVALENCES = [
    ["ি", "ী"],
    ["ু", "ূ"],
    ["ন", "ণ"],
    ["য", "য়", "য়"],
    ["র", "ব"],
    ["দ", "ধ"],
    ["ৎ", "ত"],
    ["ং", "ঙ"],
    ["শ", "ষ", "স"],
    ["ে", "ো"],
    ["া", "ে"],
]

NOISE_FRAGMENTS = {
    "ট",
    "টট",
    "কক",
    "গে",
    "জে",
    "দে",
    "মে",
    "তে",
    "রে",
    "সস",
    "এস",
}

DEFAULT_PAGE_TYPES = {
    "normal_poem_page",
    "poem_or_text_page",
    "poem_start_or_short_page",
}

LOOSE_MARKS_RE = re.compile(r"[ঁংঃািীুূৃেৈোৌ্]")


def normalize_spacing(text: str) -> str:
    text = (text or "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    return re.sub(r"\s+", " ", text).strip()


def token_key(token: str) -> str:
    key = token.translate(BANGLA_TO_ASCII)
    for group in OCR_EQUIVALENCES:
        canonical = group[0]
        for variant in group[1:]:
            key = key.replace(variant, canonical)
    return key


def loose_key(token: str) -> str:
    return LOOSE_MARKS_RE.sub("", token_key(token))


def words(text: str, min_len: int) -> list[str]:
    items = []
    for match in WORD_RE.finditer(normalize_spacing(text)):
        token = match.group(0)
        if len(token) < min_len:
            continue
        if DIGIT_RE.match(token):
            continue
        if token in NOISE_FRAGMENTS:
            continue
        items.append(token)
    return items


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


def progress(iterable, **kwargs):
    if tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


def build_lexicon(
    poems_dir: Path,
    min_len: int,
) -> tuple[Counter[str], dict[str, Counter[str]], dict[str, Counter[str]]]:
    lexicon: Counter[str] = Counter()
    for path in sorted(glob.glob(str(poems_dir / "*.json"))):
        poem = read_json(Path(path))
        if poem.get("poet_id") != "jibanananda-das":
            continue
        text = "\n".join(
            part
            for part in [
                poem.get("title_bn") or "",
                poem.get("body_bn") or "",
                poem.get("source_edition") or "",
            ]
            if part
        )
        lexicon.update(words(text, min_len))

    by_key: dict[str, Counter[str]] = defaultdict(Counter)
    by_loose_key: dict[str, Counter[str]] = defaultdict(Counter)
    for token, count in lexicon.items():
        by_key[token_key(token)][token] += count
        loose = loose_key(token)
        if len(loose) >= 2:
            by_loose_key[loose][token] += count
    return lexicon, by_key, by_loose_key


def page_text(row: dict[str, Any]) -> str:
    profile_text = "\n".join(
        profile.get("text") or "" for profile in row.get("ocr_profiles") or []
    )
    return "\n".join(
        part
        for part in [
            row.get("raw_ocr") or "",
            row.get("raw_pdftotext") or "",
            profile_text,
        ]
        if part
    )


def short_context(text: str, token: str, width: int = 36) -> str:
    index = text.find(token)
    if index < 0:
        return ""
    start = max(0, index - width)
    end = min(len(text), index + len(token) + width)
    return normalize_spacing(text[start:end])


def close_suggestions(
    token: str,
    lexicon: Counter[str],
    by_key: dict[str, Counter[str]],
    by_loose_key: dict[str, Counter[str]],
    max_suggestions: int,
) -> list[dict[str, Any]]:
    exact_key_matches = by_key.get(token_key(token), Counter())
    suggestions: list[tuple[float, str, int, str]] = []

    for candidate, count in exact_key_matches.most_common(max_suggestions * 2):
        if candidate != token:
            suggestions.append((1.0, candidate, count, "ocr_equivalence"))

    loose = loose_key(token)
    if len(suggestions) < max_suggestions and len(loose) >= 2:
        for candidate, count in by_loose_key.get(loose, Counter()).most_common(max_suggestions * 2):
            if candidate != token:
                suggestions.append((0.94, candidate, count, "loose_vowel_key"))

    if len(suggestions) < max_suggestions:
        first = token[:1]
        token_len = len(token)
        for candidate, count in lexicon.most_common():
            if candidate == token:
                continue
            if first and candidate[:1] != first:
                continue
            if abs(len(candidate) - token_len) > 2:
                continue
            ratio = SequenceMatcher(None, token, candidate).ratio()
            if ratio >= 0.78:
                suggestions.append((ratio, candidate, count, "edit_similarity"))
            if len(suggestions) >= max_suggestions * 6:
                break

    dedup: dict[str, tuple[float, int, str]] = {}
    for score, candidate, count, basis in suggestions:
        previous = dedup.get(candidate)
        if previous is None or score > previous[0]:
            dedup[candidate] = (score, count, basis)

    ranked = sorted(dedup.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))
    return [
        {
            "token": candidate,
            "score": round(score, 3),
            "lexicon_count": count,
            "basis": basis,
        }
        for candidate, (score, count, basis) in ranked[:max_suggestions]
    ]


def audit_pages(
    pages: list[dict[str, Any]],
    lexicon: Counter[str],
    by_key: dict[str, Counter[str]],
    min_len: int,
    max_examples: int,
    book_id: str | None,
    page_types: set[str],
) -> tuple[Counter[str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    suspicious_counts: Counter[str] = Counter()
    token_meta: dict[str, dict[str, Any]] = {}
    page_reports: list[dict[str, Any]] = []

    iterable = pages
    if book_id:
        iterable = [row for row in pages if row.get("book_id") == book_id]
    iterable = [row for row in iterable if row.get("page_type") in page_types]

    for row in progress(iterable, desc="auditing OCR lexicon", unit="page"):
        text = page_text(row)
        page_words = words(text, min_len)
        if not page_words:
            continue

        unknown = []
        for token in page_words:
            if token in lexicon:
                continue
            suspicious_counts[token] += 1
            unknown.append(token)
            meta = token_meta.setdefault(
                token,
                {
                    "token": token,
                    "key": token_key(token),
                    "books": Counter(),
                    "pages": [],
                    "contexts": [],
                },
            )
            meta["books"][row.get("book_id") or ""] += 1
            if len(meta["pages"]) < max_examples:
                meta["pages"].append(
                    {
                        "book_id": row.get("book_id"),
                        "collection_bn": row.get("collection_bn"),
                        "scan_page": row.get("scan_page"),
                        "printed_page": row.get("printed_page_fixed"),
                    }
                )
            if len(meta["contexts"]) < max_examples:
                context = short_context(text, token)
                if context:
                    meta["contexts"].append(context)

        if unknown:
            page_reports.append(
                {
                    "book_id": row.get("book_id"),
                    "collection_bn": row.get("collection_bn"),
                    "scan_page": row.get("scan_page"),
                    "printed_page": row.get("printed_page_fixed"),
                    "page_type": row.get("page_type"),
                    "unknown_token_count": len(unknown),
                    "token_count": len(page_words),
                    "unknown_ratio": round(len(unknown) / len(page_words), 3),
                    "top_unknown_tokens": [token for token, _ in Counter(unknown).most_common(12)],
                }
            )

    return suspicious_counts, token_meta, page_reports


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit OCR tokens against poem-text lexicon.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.layout.jsonl")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--output", default="metadata_reports/ocr-lexicon-audit.current.json")
    parser.add_argument("--min-len", type=int, default=3)
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--max-examples", type=int, default=4)
    parser.add_argument("--max-suggestions", type=int, default=5)
    parser.add_argument("--book-id", default=None)
    parser.add_argument(
        "--page-types",
        default=",".join(sorted(DEFAULT_PAGE_TYPES)),
        help="Comma-separated page types to audit.",
    )
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    global tqdm
    if args.no_progress:
        tqdm = None

    page_types = {item.strip() for item in args.page_types.split(",") if item.strip()}
    lexicon, by_key, by_loose_key = build_lexicon(Path(args.poems_dir), args.min_len)
    pages = read_jsonl(Path(args.page_corpus))
    suspicious_counts, token_meta, page_reports = audit_pages(
        pages,
        lexicon,
        by_key,
        args.min_len,
        args.max_examples,
        args.book_id,
        page_types,
    )
    pages_scanned = len(
        [
            row
            for row in pages
            if (not args.book_id or row.get("book_id") == args.book_id)
            and row.get("page_type") in page_types
        ]
    )

    suspicious_tokens = []
    for token, count in suspicious_counts.most_common(args.top):
        meta = token_meta[token]
        suspicious_tokens.append(
            {
                "token": token,
                "count": count,
                "key": meta["key"],
                "books": dict(meta["books"].most_common()),
                "pages": meta["pages"],
                "contexts": meta["contexts"],
                "suggestions": close_suggestions(
                    token,
                    lexicon,
                    by_key,
                    by_loose_key,
                    args.max_suggestions,
                ),
            }
        )

    worst_pages = sorted(
        page_reports,
        key=lambda row: (-row["unknown_ratio"], -row["unknown_token_count"], row["book_id"] or "", row["scan_page"] or 0),
    )[: args.top]

    report = {
        "summary": {
            "page_corpus": args.page_corpus,
            "poems_dir": args.poems_dir,
            "book_id": args.book_id,
            "page_types": sorted(page_types),
            "lexicon_size": len(lexicon),
            "lexicon_key_count": len(by_key),
            "lexicon_loose_key_count": len(by_loose_key),
            "pages_scanned": pages_scanned,
            "unique_suspicious_tokens": len(suspicious_counts),
            "suspicious_token_instances": sum(suspicious_counts.values()),
            "min_len": args.min_len,
        },
        "suspicious_tokens": suspicious_tokens,
        "worst_pages": worst_pages,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
