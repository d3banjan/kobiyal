#!/usr/bin/env python3
"""Audit and optionally apply explicit source markers embedded in poem bodies.

This is intentionally narrow. It trusts only marker-like trailing source notes,
not ordinary mentions of a collection title inside the poem text. It can improve
collection/phase classification, but it never writes printed-page citations.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


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


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)


def marker_patterns(marker: str) -> list[re.Pattern[str]]:
    escaped = re.escape(marker)
    return [
        re.compile(rf"(?P<prefix>\s*)[#＃]\s*{escaped}\s*$"),
        re.compile(rf"(?P<prefix>\s*)[—-]?\s*[\(\[]\s*{escaped}\s*[\)\]]\s*$"),
        re.compile(rf"(?P<prefix>\s*)(?:গ্রন্থ|কাব্যগ্রন্থ)\s*[:：-]\s*{escaped}\s*$"),
        re.compile(rf"(?P<prefix>\s*)[\(\[]\s*{escaped}\s+কাব্যগ্রন্থ\s*[\)\]]\s*$"),
    ]


def find_marker(body: str) -> dict[str, Any] | None:
    lines = body.splitlines()
    candidates = []
    for line_index, line in enumerate(lines):
        for marker, meta in SOURCE_MARKERS.items():
            for pattern in marker_patterns(marker):
                match = pattern.search(line)
                if not match:
                    continue
                candidates.append(
                    {
                        "source_edition": marker,
                        "source_year": meta["source_year"],
                        "phase_id": meta["phase_id"],
                        "line_index": line_index,
                        "line": line.strip(),
                        "marker_start": match.start(),
                        "marker_end": match.end(),
                    }
                )
    if len(candidates) != 1:
        return None
    return candidates[0]


def remove_marker(body: str, marker: dict[str, Any]) -> str:
    lines = body.splitlines()
    index = int(marker["line_index"])
    line = lines[index]
    cleaned = (line[: int(marker["marker_start"])] + line[int(marker["marker_end"]) :]).rstrip()
    if cleaned:
        lines[index] = cleaned
    else:
        lines.pop(index)
    return "\n".join(lines)


def audit_poems(poems_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(poems_dir.glob("*.json")):
        poem = read_json(path)
        if poem.get("poet_id") != "jibanananda-das":
            continue
        marker = find_marker(str(poem.get("body_bn") or ""))
        if marker is None:
            continue
        current_source = poem.get("source_edition")
        if current_source == UNKNOWN_COLLECTION:
            status = "applies_to_unknown"
        elif current_source == marker["source_edition"]:
            status = "already_matching"
        else:
            status = "conflicting_existing"
        rows.append(
            {
                "filename": path.name,
                "poem_id": poem.get("id"),
                "title_bn": poem.get("title_bn"),
                "current_source_edition": current_source,
                "current_source_year": poem.get("source_year"),
                "current_phase_id": poem.get("phase_id"),
                "status": status,
                "source_url": poem.get("source_url"),
                **marker,
            }
        )
    return rows


def apply_rows(poems_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed = []
    for row in rows:
        if row["status"] != "applies_to_unknown":
            continue
        path = poems_dir / str(row["filename"])
        poem = read_json(path)
        marker = find_marker(str(poem.get("body_bn") or ""))
        if marker is None or marker["source_edition"] != row["source_edition"]:
            continue
        poem["phase_id"] = row["phase_id"]
        poem["source_edition"] = row["source_edition"]
        poem["source_year"] = row["source_year"]
        poem["body_bn"] = remove_marker(str(poem.get("body_bn") or ""), marker)
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
        "# Embedded Source Audit",
        "",
        "Review of explicit source markers embedded in poem bodies.",
        "",
        "## Summary",
        "",
    ]
    for key, value in payload["summary"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| file | title | status | current source | marker source | marker line |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in payload["rows"]:
        line = str(row.get("line") or "").replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("filename") or ""),
                    str(row.get("title_bn") or ""),
                    str(row.get("status") or ""),
                    str(row.get("current_source_edition") or ""),
                    str(row.get("source_edition") or ""),
                    line,
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit explicit source markers embedded in poem bodies.")
    parser.add_argument("--poems-dir", default="src/data/poems")
    parser.add_argument("--output", default="metadata_reports/embedded-source-audit.current.json")
    parser.add_argument("--markdown-output", default="metadata_reports/embedded-source-audit.current.md")
    parser.add_argument("--apply", action="store_true", help="Apply safe unknown-source classifications.")
    args = parser.parse_args()

    poems_dir = Path(args.poems_dir)
    rows = audit_poems(poems_dir)
    changed = apply_rows(poems_dir, rows) if args.apply else []
    payload = {
        "summary": {
            "row_count": len(rows),
            "applies_to_unknown_count": sum(1 for row in rows if row["status"] == "applies_to_unknown"),
            "already_matching_count": sum(1 for row in rows if row["status"] == "already_matching"),
            "conflicting_existing_count": sum(1 for row in rows if row["status"] == "conflicting_existing"),
            "changed_count": len(changed),
            "applied": args.apply,
            "note": "Explicit embedded source markers only; no printed-page citations are written.",
        },
        "rows": rows,
        "changed": changed,
    }
    write_report(Path(args.output), payload)
    Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.markdown_output).write_text(markdown_report(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
