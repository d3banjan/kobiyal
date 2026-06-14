#!/usr/bin/env python3
"""Audit explicit collection labels on source URLs.

This is a secondary-source classifier. It may fill source edition/year/phase
when the current source URL itself explicitly labels a poem with a known
collection, but it never writes printed-page citations.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


UNKNOWN_COLLECTION = "সংকলন অজানা"
SOURCE_MARKERS = {
    "ঝরা পালক": {"source_year": 1927, "phase_id": "jhara-palak"},
    "ধূসর পাণ্ডুলিপি": {"source_year": 1936, "phase_id": "dhusar-pandulipi"},
    "বনলতা সেন": {"source_year": 1942, "phase_id": "banalata-sen"},
    "মহাপৃথিবী": {"source_year": 1944, "phase_id": "mahaprithibi-timir"},
    "সাতটি তারার তিমির": {"source_year": 1948, "phase_id": "mahaprithibi-timir"},
    "রূপসী বাংলা": {"source_year": 1957, "phase_id": "rupasi-bangla"},
    "বেলা অবেলা কালবেলা": {"source_year": 1961, "phase_id": "posthumous-manuscript"},
    "শ্রেষ্ঠ কবিতা": {"source_year": 1954, "phase_id": "posthumous-manuscript"},
    "আলোপৃথিবী": {"source_year": 1981, "phase_id": "posthumous-manuscript"},
    "অগ্রন্থিত কবিতা": {"source_year": None, "phase_id": "posthumous-manuscript"},
    "অপ্রকাশিত কবিতা": {"source_year": None, "phase_id": "posthumous-manuscript"},
}
MARKER_RE = re.compile(
    r"কাব্যগ্রন্থ\s*[-:：]\s*(?P<marker>"
    + "|".join(re.escape(marker) for marker in SOURCE_MARKERS)
    + r")"
)


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_url(url: str) -> str:
    return url.replace("https://www.bangla-kobita.com/jibanananda/", "https://www.bangla-kobita.com/jibananandadas/")


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_url(url: str, timeout: int, user_agent: str) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def find_source_marker(text: str) -> dict[str, Any] | None:
    matches = [match.group("marker") for match in MARKER_RE.finditer(text)]
    unique_matches = sorted(set(matches))
    if len(unique_matches) != 1:
        return None
    marker = unique_matches[0]
    return {
        "source_edition": marker,
        "source_year": SOURCE_MARKERS[marker]["source_year"],
        "phase_id": SOURCE_MARKERS[marker]["phase_id"],
    }


def status_for_marker(poem: dict[str, Any], marker: dict[str, Any] | None) -> str:
    current_source = poem.get("source_edition")
    if marker is None:
        return "no_explicit_marker"
    if current_source == UNKNOWN_COLLECTION:
        return "applies_to_unknown"
    if current_source == marker["source_edition"]:
        return "already_matching"
    return "conflicting_existing"


def audit_poems(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(Path(args.poems_dir).glob("*.json")):
        poem = read_json(path)
        if poem.get("poet_id") != "jibanananda-das":
            continue
        if not args.include_known and poem.get("source_edition") != UNKNOWN_COLLECTION:
            continue
        raw_url = str(poem.get("source_url") or "")
        canonical_url = normalize_url(raw_url)
        row: dict[str, Any] = {
            "filename": path.name,
            "poem_id": poem.get("id"),
            "title_bn": poem.get("title_bn"),
            "current_source_edition": poem.get("source_edition"),
            "source_url": raw_url,
            "canonical_source_url": canonical_url,
            "status": "no_checked_url",
        }
        if args.domain and args.domain not in canonical_url:
            rows.append(row)
            continue
        if not canonical_url:
            rows.append(row)
            continue
        try:
            page_text = html_to_text(fetch_url(canonical_url, args.timeout, args.user_agent))
        except (OSError, URLError, TimeoutError) as exc:
            row["status"] = "fetch_error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue

        marker = find_source_marker(page_text)
        row["status"] = status_for_marker(poem, marker)
        if marker is not None:
            row.update(marker)
        rows.append(row)
        if args.sleep:
            time.sleep(args.sleep)
    return rows


def apply_rows(poems_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed = []
    for row in rows:
        if row.get("status") != "applies_to_unknown":
            continue
        path = poems_dir / str(row["filename"])
        poem = read_json(path)
        if poem.get("source_edition") != UNKNOWN_COLLECTION:
            continue
        poem["source_edition"] = row["source_edition"]
        poem["source_year"] = row["source_year"]
        poem["phase_id"] = row["phase_id"]
        if row.get("canonical_source_url"):
            poem["source_url"] = row["canonical_source_url"]
        write_json(path, poem)
        changed.append(
            {
                "filename": row["filename"],
                "poem_id": row["poem_id"],
                "title_bn": row["title_bn"],
                "source_edition": row["source_edition"],
                "source_year": row["source_year"],
                "phase_id": row["phase_id"],
            }
        )
    return changed


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Source URL Marker Audit",
        "",
        "Review of explicit collection labels on existing poem source URLs.",
        "",
        "## Summary",
        "",
    ]
    for key, value in payload["summary"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Applicable Rows",
            "",
            "| file | title | marker source | year | source URL |",
            "|---|---|---|---:|---|",
        ]
    )
    for row in payload["rows"]:
        if row.get("status") not in {"applies_to_unknown", "conflicting_existing"}:
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or "").replace("|", "\\|"),
                    str(row.get("source_edition") or ""),
                    str(row.get("source_year") or ""),
                    str(row.get("canonical_source_url") or row.get("source_url") or ""),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit explicit source labels on source URLs.")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--output", default="metadata_reports/source-url-marker-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/source-url-marker-audit.current.md")
    parser.add_argument("--domain", default="bangla-kobita.com")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--user-agent", default="Mozilla/5.0")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-known", action="store_true", help="Also audit already-classified rows for conflicts.")
    args = parser.parse_args()

    rows = audit_poems(args)
    changed = apply_rows(Path(args.poems_dir), rows) if args.apply else []
    payload = {
        "summary": {
            "row_count": len(rows),
            "applies_to_unknown_count": sum(1 for row in rows if row.get("status") == "applies_to_unknown"),
            "already_matching_count": sum(1 for row in rows if row.get("status") == "already_matching"),
            "conflicting_existing_count": sum(1 for row in rows if row.get("status") == "conflicting_existing"),
            "no_explicit_marker_count": sum(1 for row in rows if row.get("status") == "no_explicit_marker"),
            "fetch_error_count": sum(1 for row in rows if row.get("status") == "fetch_error"),
            "changed_count": len(changed),
            "applied": args.apply,
            "note": "Secondary source marker only; no printed-page citations are written.",
        },
        "rows": rows,
        "changed": changed,
    }
    write_json(Path(args.output), payload)
    Path(args.markdown_output).write_text(markdown_report(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
