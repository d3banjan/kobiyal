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
WESTERN_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
INLINE_DIGIT_RE = re.compile(r"[\u09E6-\u09EF0-9]")
STANDALONE_DIGIT_LINE_RE = re.compile(r"^[\u09E6-\u09EF0-9]+$")
REPLACEMENT_CHAR_RE = re.compile(r"[�□■▪]")
DASH_DIVIDER_LINE_RE = re.compile(r"^-{5,}$")
SINGLE_LEADING_STANZA_DASH_RE = re.compile(r"^-(?!-)\s*\S")
MULTI_LEADING_DASH_RE = re.compile(r"^--+\S")
LEADING_EM_DASH_RE = re.compile(r"^—\s*\S")

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


def load_jibanananda_poems(poems_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    poems = []
    for path in sorted(glob.glob(str(poems_dir / "*.json"))):
        poem = read_json(Path(path))
        if poem.get("poet_id") != "jibanananda-das":
            continue
        poems.append((Path(path), poem))
    return poems


def progress(iterable, **kwargs):
    if tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


def build_lexicon(
    poems_dir: Path,
    min_len: int,
) -> tuple[Counter[str], dict[str, Counter[str]], dict[str, Counter[str]]]:
    lexicon: Counter[str] = Counter()
    for _, poem in load_jibanananda_poems(poems_dir):
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
    exclude_counts: Counter[str] | None = None,
    allow_edit_similarity: bool = True,
) -> list[dict[str, Any]]:
    exact_key_matches = by_key.get(token_key(token), Counter())
    suggestions: list[tuple[float, str, int, str]] = []

    def effective_count(candidate: str, count: int) -> int:
        if exclude_counts is None:
            return count
        return max(0, count - exclude_counts.get(candidate, 0))

    for candidate, count in exact_key_matches.most_common(max_suggestions * 2):
        usable_count = effective_count(candidate, count)
        if candidate != token and usable_count > 0:
            suggestions.append((1.0, candidate, usable_count, "ocr_equivalence"))

    loose = loose_key(token)
    if len(suggestions) < max_suggestions and len(loose) >= 2:
        for candidate, count in by_loose_key.get(loose, Counter()).most_common(max_suggestions * 2):
            usable_count = effective_count(candidate, count)
            if candidate != token and usable_count > 0:
                suggestions.append((0.94, candidate, usable_count, "loose_vowel_key"))

    if len(suggestions) < max_suggestions and allow_edit_similarity:
        first = token[:1]
        token_len = len(token)
        for candidate, count in lexicon.most_common():
            if candidate == token:
                continue
            usable_count = effective_count(candidate, count)
            if usable_count <= 0:
                continue
            if first and candidate[:1] != first:
                continue
            if abs(len(candidate) - token_len) > 2:
                continue
            ratio = SequenceMatcher(None, token, candidate).ratio()
            if ratio >= 0.78:
                suggestions.append((ratio, candidate, usable_count, "edit_similarity"))
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


def substitution_candidate(
    token: str,
    count: int,
    suggestions: list[dict[str, Any]],
    min_count: int,
    min_lexicon_count: int,
    min_score: float,
    dominance_ratio: float,
) -> dict[str, Any] | None:
    if count < min_count or not suggestions:
        return None

    first = suggestions[0]
    first_score = float(first["score"])
    first_count = int(first["lexicon_count"])
    if first_score < min_score or first_count < min_lexicon_count:
        return None
    if first["token"] == token:
        return None

    if len(suggestions) > 1:
        second = suggestions[1]
        second_score = float(second["score"])
        second_count = int(second["lexicon_count"])
        if second_score == first_score and first_count < int(second_count * dominance_ratio):
            return None

    return {
        "from": token,
        "to": first["token"],
        "count": count,
        "score": first_score,
        "lexicon_count": first_count,
        "basis": first["basis"],
    }


def poem_text_for_lexicon(poem: dict[str, Any]) -> str:
    return "\n".join(
        part
        for part in [
            poem.get("title_bn") or "",
            poem.get("body_bn") or "",
            poem.get("source_edition") or "",
        ]
        if part
    )


def line_contexts_for_token(text: str, token: str, max_examples: int) -> list[dict[str, Any]]:
    contexts = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if token not in line:
            continue
        contexts.append({"line_no": line_no, "line": normalize_spacing(line)})
        if len(contexts) >= max_examples:
            break
    return contexts


def poem_quality_report(
    poems: list[tuple[Path, dict[str, Any]]],
    lexicon: Counter[str],
    by_key: dict[str, Counter[str]],
    by_loose_key: dict[str, Counter[str]],
    min_len: int,
    max_suggestions: int,
    max_examples: int,
    top: int,
    min_score: float,
    min_lexicon_count: int,
    dominance_ratio: float,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    token_candidates: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()

    for path, poem in poems:
        body = poem.get("body_bn") or ""
        file_counts = Counter(words(poem_text_for_lexicon(poem), min_len))
        for line_no, line in enumerate(body.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue

            checks = [
                ("western_alnum", "review", WESTERN_ALNUM_RE.search(line)),
                ("replacement_char", "review", REPLACEMENT_CHAR_RE.search(line)),
            ]
            if STANDALONE_DIGIT_LINE_RE.match(stripped):
                checks.append(("section_number_line", "info", True))
            elif INLINE_DIGIT_RE.search(line):
                checks.append(("inline_digit", "review", True))
            if DASH_DIVIDER_LINE_RE.match(stripped):
                checks.append(("dash_divider_line", "info", True))
            elif SINGLE_LEADING_STANZA_DASH_RE.match(stripped):
                checks.append(("leading_stanza_dash", "info", True))
            elif MULTI_LEADING_DASH_RE.match(stripped):
                checks.append(("multi_leading_dash", "review", True))
            elif LEADING_EM_DASH_RE.match(stripped):
                checks.append(("leading_em_dash", "info", True))

            for kind, severity, matched in checks:
                if not matched:
                    continue
                issue_counts[kind] += 1
                issues.append(
                    {
                        "kind": kind,
                        "severity": severity,
                        "filename": path.name,
                        "poem_id": poem.get("id"),
                        "title_bn": poem.get("title_bn"),
                        "line_no": line_no,
                        "line": normalize_spacing(line),
                    }
                )

        body_counts = Counter(words(body, min_len))
        for token, count in body_counts.items():
            # If the same exact spelling appears elsewhere in the corpus, do not
            # flag it as a site-text correction candidate.
            if lexicon[token] - file_counts.get(token, 0) > 0:
                continue
            suggestions = close_suggestions(
                token,
                lexicon,
                by_key,
                by_loose_key,
                max_suggestions,
                exclude_counts=file_counts,
                allow_edit_similarity=False,
            )
            if not suggestions:
                continue

            first = suggestions[0]
            if float(first["score"]) < min_score or int(first["lexicon_count"]) < min_lexicon_count:
                continue
            second = suggestions[1] if len(suggestions) > 1 else None
            second_count = int(second["lexicon_count"]) if second else 0
            is_high_priority = (
                second is None
                or int(first["lexicon_count"]) >= max(1, second_count) * dominance_ratio
            )
            token_candidates.append(
                {
                    "token": token,
                    "count_in_poem": count,
                    "review_priority": "high" if is_high_priority else "normal",
                    "filename": path.name,
                    "poem_id": poem.get("id"),
                    "title_bn": poem.get("title_bn"),
                    "suggestions": suggestions,
                    "contexts": line_contexts_for_token(body, token, max_examples),
                }
            )

    token_candidates.sort(
        key=lambda row: (
            row["review_priority"] != "high",
            -float(row["suggestions"][0]["score"]),
            -int(row["suggestions"][0]["lexicon_count"]),
            row["filename"],
            row["token"],
        )
    )
    issues.sort(key=lambda row: (row["severity"] != "review", row["filename"], row["line_no"], row["kind"]))

    return {
        "summary": {
            "poem_count": len(poems),
            "issue_count": len(issues),
            "issue_counts": dict(issue_counts.most_common()),
            "token_candidate_count": len(token_candidates),
            "high_priority_token_candidate_count": sum(
                1 for row in token_candidates if row["review_priority"] == "high"
            ),
            "min_score": min_score,
            "min_lexicon_count": min_lexicon_count,
            "dominance_ratio": dominance_ratio,
            "note": "Review-only report; it does not mutate poem JSON.",
        },
        "issues": issues[:top],
        "token_candidates": token_candidates[:top],
    }


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
    parser.add_argument("--substitution-min-count", type=int, default=8)
    parser.add_argument("--substitution-min-lexicon-count", type=int, default=20)
    parser.add_argument("--substitution-min-score", type=float, default=0.94)
    parser.add_argument("--substitution-dominance-ratio", type=float, default=2.0)
    parser.add_argument(
        "--substitutions-output",
        default="metadata_reports/ocr-lexicon-substitutions.current.json",
    )
    parser.add_argument(
        "--poem-quality-output",
        default="metadata_reports/poem-text-quality.current.json",
        help="Review-only site poem-body quality report.",
    )
    parser.add_argument("--no-poem-quality", action="store_true")
    parser.add_argument("--poem-quality-min-score", type=float, default=0.94)
    parser.add_argument("--poem-quality-min-lexicon-count", type=int, default=3)
    parser.add_argument("--poem-quality-dominance-ratio", type=float, default=3.0)
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
    poems = load_jibanananda_poems(Path(args.poems_dir))
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
        suggestions = close_suggestions(
            token,
            lexicon,
            by_key,
            by_loose_key,
            args.max_suggestions,
        )
        suspicious_tokens.append(
            {
                "token": token,
                "count": count,
                "key": meta["key"],
                "books": dict(meta["books"].most_common()),
                "pages": meta["pages"],
                "contexts": meta["contexts"],
                "suggestions": suggestions,
            }
        )

    substitution_candidates = []
    for token, count in suspicious_counts.most_common():
        if count < args.substitution_min_count:
            break
        suggestions = close_suggestions(
            token,
            lexicon,
            by_key,
            by_loose_key,
            args.max_suggestions,
        )
        candidate = substitution_candidate(
            token,
            count,
            suggestions,
            args.substitution_min_count,
            args.substitution_min_lexicon_count,
            args.substitution_min_score,
            args.substitution_dominance_ratio,
        )
        if candidate is not None:
            substitution_candidates.append(candidate)

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
            "substitution_candidate_count": len(substitution_candidates),
        },
        "suspicious_tokens": suspicious_tokens,
        "substitution_candidates": substitution_candidates,
        "worst_pages": worst_pages,
    }

    if not args.no_poem_quality:
        quality_report = poem_quality_report(
            poems,
            lexicon,
            by_key,
            by_loose_key,
            args.min_len,
            args.max_suggestions,
            args.max_examples,
            args.top,
            args.poem_quality_min_score,
            args.poem_quality_min_lexicon_count,
            args.poem_quality_dominance_ratio,
        )
        quality_output = Path(args.poem_quality_output)
        quality_output.parent.mkdir(parents=True, exist_ok=True)
        quality_output.write_text(
            json.dumps(quality_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["summary"]["poem_quality_output"] = str(quality_output)
        report["summary"]["poem_quality_token_candidate_count"] = quality_report["summary"][
            "token_candidate_count"
        ]
        report["summary"]["poem_quality_high_priority_token_candidate_count"] = quality_report[
            "summary"
        ]["high_priority_token_candidate_count"]
        report["summary"]["poem_quality_issue_count"] = quality_report["summary"]["issue_count"]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    substitutions_output = Path(args.substitutions_output)
    substitutions_output.parent.mkdir(parents=True, exist_ok=True)
    substitutions_output.write_text(
        json.dumps(
            {
                "summary": report["summary"],
                "substitutions": {
                    item["from"]: item["to"] for item in substitution_candidates
                },
                "candidates": substitution_candidates,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
