#!/usr/bin/env python3
"""Verify rendered poem pages expose printed book-page citations.

This checks the built Astro output against the poem JSON. It is deliberately
small and dependency-free so CI can run it after `bun run build`.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def duplicate_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(re.findall(r'"(jibanananda-[^"]+)"', path.read_text(encoding="utf-8")))


def rendered_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def bn(value: int) -> str:
    return str(value).translate(BN_DIGITS)


def page_label(source: dict[str, Any]) -> str:
    if source.get("page_label_bn"):
        return str(source["page_label_bn"])
    start = source.get("page_start")
    end = source.get("page_end")
    if isinstance(start, int) and isinstance(end, int) and start != end:
        return f"{bn(start)}-{bn(end)}"
    if isinstance(start, int):
        return bn(start)
    if isinstance(end, int):
        return bn(end)
    return ""


def primary_sources(poem: dict[str, Any]) -> list[dict[str, Any]]:
    return [source for source in poem.get("book_sources") or [] if source.get("role") == "primary"]


def verify_poem_page(poem: dict[str, Any], filename: str, dist_dir: Path) -> list[str]:
    errors = []
    sources = primary_sources(poem)
    if not sources:
        return errors

    page_path = dist_dir / "poems" / str(poem["id"]) / "index.html"
    if not page_path.exists():
        return [f"{filename}: missing rendered page {page_path}"]

    text = rendered_text(page_path)
    if "মুদ্রিত বইয়ের সূত্র" not in text:
        errors.append(f"{filename}: missing printed-book sources heading")
    if "স্ক্যান পৃষ্ঠা" in text or "ডিজিটাল পৃষ্ঠা" in text:
        errors.append(f"{filename}: rendered page includes non-printed page-basis label")

    for index, source in enumerate(sources):
        if source.get("page_basis") != "printed_page":
            errors.append(f"{filename}: primary source {index} is not page_basis=printed_page")
            continue
        label = page_label(source)
        if not label:
            errors.append(f"{filename}: primary source {index} lacks page label")
        for expected in (
            str(source.get("title_bn") or ""),
            "পৃষ্ঠা-ভিত্তি",
            "মুদ্রিত পৃষ্ঠা",
            label,
        ):
            if expected and expected not in text:
                errors.append(f"{filename}: rendered page missing {expected!r}")
        year = source.get("publication_year")
        if isinstance(year, int) and bn(year) not in text:
            errors.append(f"{filename}: rendered page missing publication year {bn(year)}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify rendered printed book-page citations.")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--duplicates-source", default="src/lib/content.ts")
    parser.add_argument("--dist-dir", default="dist")
    parser.add_argument("--poet-id", default="jibanananda-das")
    args = parser.parse_args()

    poems_dir = Path(args.poems_dir)
    duplicates = duplicate_ids(Path(args.duplicates_source))
    errors: list[str] = []
    checked = 0
    source_count = 0
    for path in sorted(poems_dir.glob("*.json")):
        poem = read_json(path)
        if poem.get("poet_id") != args.poet_id:
            continue
        if poem.get("id") in duplicates:
            continue
        sources = primary_sources(poem)
        if not sources:
            continue
        checked += 1
        source_count += len(sources)
        errors.extend(verify_poem_page(poem, path.name, Path(args.dist_dir)))

    if errors:
        for error in errors[:80]:
            print(error)
        if len(errors) > 80:
            print(f"... {len(errors) - 80} more errors")
        return 1

    print(
        json.dumps(
            {
                "verified": True,
                "poem_pages_checked": checked,
                "primary_printed_sources_checked": source_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
