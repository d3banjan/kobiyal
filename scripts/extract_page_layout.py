#!/usr/bin/env python3
"""Extract Tesseract TSV geometry for page-level layout evidence.

This script writes sidecar JSONL only. It never mutates poem JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover - checked at runtime.
    Image = None

from tqdm import tqdm

BANGLA_DIGITS = "০১২৩৪৫৬৭৮৯"
BANGLA_TO_ASCII = str.maketrans(BANGLA_DIGITS, "0123456789")
ASCII_TO_BANGLA = str.maketrans("0123456789", BANGLA_DIGITS)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def bangla_number(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value).translate(ASCII_TO_BANGLA)


def parse_int_token(text: str) -> int | None:
    digits = text.translate(BANGLA_TO_ASCII)
    digits = re.sub(r"\D", "", digits)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def compact_digit_text(text: str) -> str:
    return re.sub(r"[\s।|:;,.·\-–—_]+", "", text or "")


def image_size(path: Path) -> tuple[int, int]:
    if Image is None:
        raise SystemExit("Pillow is required for layout extraction")
    with Image.open(path) as img:
        return img.size


def run_tesseract_tsv(image_path: Path, tsv_path: Path, lang: str, psm: int) -> str:
    if tsv_path.exists():
        return tsv_path.read_text(encoding="utf-8", errors="ignore")
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "tesseract",
            str(image_path),
            "stdout",
            "-l",
            lang,
            "--psm",
            str(psm),
            "tsv",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        tsv_path.write_text("", encoding="utf-8")
        return ""
    tsv_path.write_text(result.stdout, encoding="utf-8")
    return result.stdout


def parse_tsv(tsv_text: str) -> list[dict[str, Any]]:
    lines = tsv_text.splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = []
    for raw in lines[1:]:
        parts = raw.split("\t")
        if len(parts) < len(header):
            parts.extend([""] * (len(header) - len(parts)))
        item = dict(zip(header, parts))
        text = (item.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(item.get("conf") or -1)
            left = int(float(item.get("left") or 0))
            top = int(float(item.get("top") or 0))
            width = int(float(item.get("width") or 0))
            height = int(float(item.get("height") or 0))
        except ValueError:
            continue
        if width <= 0 or height <= 0:
            continue
        rows.append(
            {
                "level": int(float(item.get("level") or 0)),
                "block_num": int(float(item.get("block_num") or 0)),
                "par_num": int(float(item.get("par_num") or 0)),
                "line_num": int(float(item.get("line_num") or 0)),
                "word_num": int(float(item.get("word_num") or 0)),
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "conf": conf,
                "text": text,
            }
        )
    return rows


def line_records(words: list[dict[str, Any]], image_width: int, image_height: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        grouped[(word["block_num"], word["par_num"], word["line_num"])].append(word)

    lines = []
    for key, items in grouped.items():
        items.sort(key=lambda word: word["left"])
        left = min(word["left"] for word in items)
        top = min(word["top"] for word in items)
        right = max(word["left"] + word["width"] for word in items)
        bottom = max(word["top"] + word["height"] for word in items)
        text = " ".join(word["text"] for word in items).strip()
        confs = [word["conf"] for word in items if word["conf"] >= 0]
        avg_conf = sum(confs) / len(confs) if confs else None
        lines.append(
            {
                "key": list(key),
                "text": text,
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
                "right": right,
                "bottom": bottom,
                "x_center": (left + right) / 2 / image_width,
                "y_center": (top + bottom) / 2 / image_height,
                "x0": left / image_width,
                "x1": right / image_width,
                "y0": top / image_height,
                "y1": bottom / image_height,
                "word_count": len(items),
                "avg_conf": round(avg_conf, 2) if avg_conf is not None else None,
            }
        )
    lines.sort(key=lambda line: (line["top"], line["left"]))
    return lines


def printed_page_candidates(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for line in lines:
        compact = compact_digit_text(line["text"])
        if not compact or len(compact) > 3:
            continue
        if any(ch not in BANGLA_DIGITS + "0123456789" for ch in compact):
            continue
        value = parse_int_token(compact)
        if value is None or value < 1 or value > 500:
            continue
        zone = "top" if line["y_center"] < 0.22 else "bottom" if line["y_center"] > 0.78 else "middle"
        if zone == "middle":
            continue
        candidates.append(
            {
                "value": value,
                "label_bn": bangla_number(value),
                "raw": line["text"],
                "script": "bangla" if any(ch in BANGLA_DIGITS for ch in compact) else "ascii",
                "zone": zone,
                "x_center": round(line["x_center"], 4),
                "y_center": round(line["y_center"], 4),
                "bbox": [line["x0"], line["y0"], line["x1"], line["y1"]],
                "avg_conf": line.get("avg_conf"),
                "source": "tesseract_tsv_line",
            }
        )
    candidates.sort(
        key=lambda item: (
            1 if item["script"] == "bangla" else 0,
            1 if item["zone"] == "bottom" else 0,
            item["avg_conf"] or 0,
        ),
        reverse=True,
    )
    return candidates


def layout_summary(lines: list[dict[str, Any]]) -> dict[str, Any]:
    body_lines = [
        line
        for line in lines
        if 0.18 <= line["y_center"] <= 0.82 and line["word_count"] >= 2
    ]
    if body_lines:
        body_bbox = [
            round(min(line["x0"] for line in body_lines), 4),
            round(min(line["y0"] for line in body_lines), 4),
            round(max(line["x1"] for line in body_lines), 4),
            round(max(line["y1"] for line in body_lines), 4),
        ]
        median_line_width = round(median(line["width"] for line in body_lines), 2)
    else:
        body_bbox = None
        median_line_width = None

    return {
        "line_count": len(lines),
        "word_count": sum(line["word_count"] for line in lines),
        "header_line_count": sum(1 for line in lines if line["y_center"] < 0.18),
        "body_line_count": len(body_lines),
        "footer_line_count": sum(1 for line in lines if line["y_center"] > 0.82),
        "body_bbox": body_bbox,
        "median_body_line_width": median_line_width,
    }


def build_layout_record(
    page: dict[str, Any],
    image_root: Path,
    tsv_root: Path,
    lang: str,
    psm: int,
) -> dict[str, Any]:
    book_id = page["book_id"]
    physical_book_id = page.get("physical_book_id") or book_id
    scan_page = int(page["scan_page"])
    image_path = image_root / physical_book_id / f"page-{scan_page:04d}.png"
    tsv_path = tsv_root / physical_book_id / f"page-{scan_page:04d}.tsv"
    flags = []
    if not image_path.exists():
        flags.append("missing_page_image")
        return {
            "record_id": page.get("record_id"),
            "book_id": book_id,
            "physical_book_id": physical_book_id,
            "collection_bn": page.get("collection_bn"),
            "pdf_file": page.get("pdf_file"),
            "scan_page": scan_page,
            "layout_status": "missing_image",
            "printed_page_candidates": [],
            "summary": {},
            "flags": flags,
        }

    width, height = image_size(image_path)
    tsv_text = run_tesseract_tsv(image_path, tsv_path, lang, psm)
    words = parse_tsv(tsv_text)
    lines = line_records(words, width, height)
    candidates = printed_page_candidates(lines)
    summary = layout_summary(lines)

    return {
        "record_id": page.get("record_id"),
        "book_id": book_id,
        "physical_book_id": physical_book_id,
        "collection_bn": page.get("collection_bn"),
        "pdf_file": page.get("pdf_file"),
        "scan_page": scan_page,
        "image_width": width,
        "image_height": height,
        "layout_status": "ok" if words else "empty_tsv",
        "printed_page_candidates": candidates,
        "summary": summary,
        "sample_header_lines": [line["text"] for line in lines if line["y_center"] < 0.18][:4],
        "sample_footer_lines": [line["text"] for line in lines if line["y_center"] > 0.82][-4:],
        "flags": flags,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Tesseract TSV layout evidence for page corpus records.")
    parser.add_argument("--page-corpus", default="metadata_reports/page-corpus.full.repaired.jsonl")
    parser.add_argument("--image-root", default=".ocr-cache/images")
    parser.add_argument("--tsv-root", default=".ocr-cache/tsv")
    parser.add_argument("--output", default="metadata_reports/page-layout.full.jsonl")
    parser.add_argument("--book-id", action="append", help="Limit to one or more book ids.")
    parser.add_argument("--lang", default="ben")
    parser.add_argument("--psm", type=int, default=6)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if shutil.which("tesseract") is None:
        raise SystemExit("Missing required tool: tesseract")

    pages = read_jsonl(Path(args.page_corpus))
    if args.book_id:
        wanted = set(args.book_id)
        pages = [page for page in pages if page.get("book_id") in wanted]
    if args.limit is not None:
        pages = pages[: args.limit]

    by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        by_book[page.get("book_id", "unknown")].append(page)
    for book_pages in by_book.values():
        book_pages.sort(key=lambda row: int(row.get("scan_page") or 0))

    rows = []
    for book_id, book_pages in sorted(by_book.items()):
        for page in tqdm(
            book_pages,
            desc=f"{book_id} layout",
            unit="page",
            dynamic_ncols=True,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} pages [{elapsed}<{remaining}, {rate_fmt}]",
        ):
            rows.append(
                build_layout_record(
                    page,
                    Path(args.image_root),
                    Path(args.tsv_root),
                    args.lang,
                    args.psm,
                )
            )

    write_jsonl(Path(args.output), rows)
    print(f"Wrote {len(rows)} layout records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
