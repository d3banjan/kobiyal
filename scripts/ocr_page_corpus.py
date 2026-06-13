#!/usr/bin/env python3
"""Build a page-level OCR corpus for Bengali poetry scans.

This script writes sidecar JSONL records only. It never mutates poem JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for direct python without uv.
    tqdm = None

try:
    from PIL import Image, ImageFilter, ImageOps
except ImportError:  # pragma: no cover - validated by setup checks.
    Image = None
    ImageFilter = None
    ImageOps = None


BOOKS: dict[str, dict[str, Any]] = {
    "dhusar-pandulipi": {
        "collection_bn": "ধূসর পাণ্ডুলিপি",
        "source_kind": "primary_collection",
        "pdf_file": "2015.299938.Dhusar-Pandulipi.pdf",
        "txt_file": "2015.299938.Dhusar-Pandulipi_text.txt",
    },
    "banalata-sen": {
        "collection_bn": "বনলতা সেন",
        "source_kind": "primary_collection",
        "pdf_file": "2015.300501.Jibnananda-Dasher.pdf",
        "txt_file": "2015.300501.Jibnananda-Dasher_text.txt",
    },
    "satti-tarar-timir": {
        "collection_bn": "সাতটি তারার তিমির",
        "source_kind": "primary_collection",
        "pdf_file": "2015.300502.Jibnananda-Dasher.pdf",
        "txt_file": "2015.300502.Jibnananda-Dasher_text.txt",
    },
    "bela-abela-kalabela": {
        "collection_bn": "বেলা অবেলা কালবেলা",
        "source_kind": "primary_collection",
        "pdf_file": "2015.301901.Bela-Abela.pdf",
        "txt_file": "2015.301901.Bela-Abela_text.txt",
    },
    "mahaprithibi": {
        "collection_bn": "মহাপৃথিবী",
        "source_kind": "primary_collection",
        "pdf_file": "2015.302511.Maha-Prithibi.pdf",
        "txt_file": "2015.302511.Maha-Prithibi_text.txt",
    },
    "rupasi-bangla": {
        "collection_bn": "রূপসী বাংলা",
        "source_kind": "primary_collection",
        "pdf_file": "2015.303336.Rupasi-Bangla.pdf",
        "txt_file": "2015.303336.Rupasi-Bangla_text.txt",
    },
    "srestha-kabita": {
        "collection_bn": "শ্রেষ্ঠ কবিতা",
        "source_kind": "auxiliary_anthology",
        "pdf_file": "2015.298158.Jibanananda-Daser.pdf",
        "txt_file": "2015.298158.Jibanananda-Daser_text.txt",
    },
    "jibanananda-samagra": {
        "collection_bn": "জীবনানন্দ সমগ্র",
        "source_kind": "auxiliary_collection",
        "pdf_file": "2015.302331.Jibanananda-Samagra.pdf",
        "txt_file": "2015.302331.Jibanananda-Samagra_text.txt",
    },
    "chetanajagath": {
        "collection_bn": "জীবনানন্দের চেতনাজগৎ",
        "source_kind": "critical_prose",
        "pdf_file": "2015.302334.Jibananander-Chetanajagath.pdf",
        "txt_file": "2015.302334.Jibananander-Chetanajagath_text.txt",
    },
}

BANGLA_DIGITS = "০১২৩৪৫৬৭৮৯"
ASCII_TO_BANGLA = str.maketrans("0123456789", BANGLA_DIGITS)
BANGLA_TO_ASCII = str.maketrans(BANGLA_DIGITS, "0123456789")

OCR_EQUIVALENCE_CLASSES = [
    ["ি", "ী"],
    ["ু", "ূ"],
    ["ে", "ৈ"],
    ["ো", "ৌ"],
    ["ন", "ণ"],
    ["য", "য়", "য়"],
    ["র", "ব"],
    ["দ", "ধ"],
    ["ৎ", "ত"],
    ["ং", "ঙ"],
    ["।", "|", "l", "I", "১"],
]


@dataclass(frozen=True)
class PageImage:
    profile: str
    path: Path


ALL_PROFILES = ("raw", "gray", "threshold", "denoise_threshold")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def stable_id(*parts: object) -> str:
    data = "::".join(str(part) for part in parts)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:12]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def split_pages(text: str) -> list[str]:
    return text.split("\x0c")


def normalize_for_match(text: str) -> str:
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    text = text.translate(BANGLA_TO_ASCII)
    text = re.sub(r"[^\u0980-\u09FF0-9\s]+", " ", text)
    for group in OCR_EQUIVALENCE_CLASSES:
        canonical = group[0]
        for variant in group[1:]:
            text = text.replace(variant, canonical)
    return re.sub(r"\s+", " ", text).strip()


def bangla_number(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value).translate(ASCII_TO_BANGLA)


def parse_bangla_int(token: str) -> int | None:
    digits = token.translate(BANGLA_TO_ASCII)
    digits = re.sub(r"\D", "", digits)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def extract_printed_page_candidates(text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: list[dict[str, Any]] = []
    if not lines:
        return candidates

    zones = {
        "top": lines[:4],
        "bottom": lines[-4:],
    }
    for zone, zone_lines in zones.items():
        for line_idx, line in enumerate(zone_lines):
            compact = re.sub(r"[\s।|:;,.·\-–—_]+", "", line)
            if not compact or len(compact) > 3 or any(ch not in BANGLA_DIGITS + "0123456789" for ch in compact):
                continue
            for match in re.finditer(r"[০-৯]{1,3}|[0-9]{1,3}", line):
                value = parse_bangla_int(match.group(0))
                if value is None:
                    continue
                candidates.append(
                    {
                        "value": value,
                        "label_bn": bangla_number(value),
                        "zone": zone,
                        "line_index": line_idx,
                        "raw": match.group(0),
                        "script": "bangla" if any(ch in BANGLA_DIGITS for ch in match.group(0)) else "ascii",
                    }
                )
    unique: dict[tuple[int, str], dict[str, Any]] = {}
    for candidate in candidates:
        unique[(candidate["value"], candidate["zone"])] = candidate
    return list(unique.values())


def page_count(pdf_path: Path) -> int:
    result = run(["pdfinfo", str(pdf_path)])
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"Could not read page count from {pdf_path}")


def render_page(pdf_path: Path, scan_page: int, image_dir: Path, dpi: int) -> Path:
    image_dir.mkdir(parents=True, exist_ok=True)
    prefix = image_dir / f"page-{scan_page:04d}"
    output_path = image_dir / f"page-{scan_page:04d}.png"
    if output_path.exists():
        return output_path
    run(
        [
            "pdftoppm",
            "-f",
            str(scan_page),
            "-l",
            str(scan_page),
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(pdf_path),
            str(prefix),
        ]
    )
    return output_path


def parse_profiles(value: str) -> tuple[str, ...]:
    if value == "all":
        return ALL_PROFILES
    profiles = tuple(profile.strip() for profile in value.split(",") if profile.strip())
    unknown = sorted(set(profiles) - set(ALL_PROFILES))
    if unknown:
        raise SystemExit(f"Unknown OCR profile(s): {', '.join(unknown)}")
    if not profiles:
        raise SystemExit("At least one OCR profile is required")
    return profiles


def preprocess_images(raw_path: Path, out_dir: Path, profiles: tuple[str, ...]) -> list[PageImage]:
    if profiles == ("raw",):
        return [PageImage("raw", raw_path)]

    if Image is None or ImageOps is None:
        return [PageImage("raw", raw_path)]

    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    if "raw" in profiles:
        images.append(PageImage("raw", raw_path))

    img = Image.open(raw_path)
    gray = ImageOps.grayscale(img)

    if "gray" in profiles:
        gray_path = out_dir / "gray.png"
        if not gray_path.exists():
            gray.save(gray_path)
        images.append(PageImage("gray", gray_path))

    if "threshold" in profiles:
        threshold_path = out_dir / "threshold.png"
        if not threshold_path.exists():
            threshold = gray.point(lambda p: 255 if p > 175 else 0)
            threshold.save(threshold_path)
        images.append(PageImage("threshold", threshold_path))

    if "denoise_threshold" in profiles and ImageFilter is not None:
        denoise_path = out_dir / "denoise-threshold.png"
        if not denoise_path.exists():
            denoise = gray.filter(ImageFilter.MedianFilter(size=3))
            denoise = denoise.point(lambda p: 255 if p > 170 else 0)
            denoise.save(denoise_path)
        images.append(PageImage("denoise_threshold", denoise_path))

    return images


def tesseract_ocr(image_path: Path, out_base: Path, lang: str, psm: int) -> str:
    txt_path = out_base.with_suffix(".txt")
    if txt_path.exists():
        return read_text(txt_path)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "tesseract",
            str(image_path),
            str(out_base),
            "-l",
            lang,
            "--psm",
            str(psm),
        ],
        check=False,
    )
    return read_text(txt_path)


def structural_features(text: str, raw_ocr: str) -> dict[str, Any]:
    source = raw_ocr or text
    lines = [line.rstrip() for line in source.splitlines()]
    nonempty = [line.strip() for line in lines if line.strip()]
    char_count = sum(len(line) for line in nonempty)
    bengali_count = len(re.findall(r"[\u0980-\u09FF]", source))
    digit_count = len(re.findall(r"[০-৯0-9]", source))
    long_lines = [line for line in nonempty if len(line) >= 30]
    short_lines = [line for line in nonempty if 0 < len(line) <= 16]
    centeredish_lines = [
        line for line in nonempty if len(line) <= 24 and not re.search(r"[।;,:]{2,}", line)
    ]

    return {
        "line_count": len(nonempty),
        "char_count": char_count,
        "bengali_char_count": bengali_count,
        "digit_count": digit_count,
        "long_line_count": len(long_lines),
        "short_line_count": len(short_lines),
        "centeredish_line_count": len(centeredish_lines),
        "blank_ratio_estimate": 1.0 if not char_count else max(0.0, 1.0 - min(char_count / 2200, 1.0)),
        "header_occupancy": bool(nonempty[:3]),
        "footer_occupancy": bool(nonempty[-3:]),
    }


def classify_page(features: dict[str, Any], text: str, candidates: list[dict[str, Any]]) -> str:
    line_count = features["line_count"]
    char_count = features["char_count"]
    long_line_count = features["long_line_count"]
    centeredish = features["centeredish_line_count"]
    normalized = normalize_for_match(text)

    if char_count < 25 or line_count <= 1:
        return "blank_or_near_blank"
    if re.search(r"প্রকাশক|মুদ্রক|প্রথম\s*মুদ্রণ|প্রথম\s*সংস্করণ|প্রচ্ছদ", normalized):
        return "publisher_page"
    if re.search(r"^[\"'‘’“”।]*(সূচ|সুচ)", normalized):
        return "front_matter"
    if re.search(r"উৎসর্গ|ভূমিকা|রচনাকাল", normalized) and long_line_count < 8:
        return "front_matter"
    if re.search(r"জীবনানন্দেরচেতনাজগৎ|প্রবন্ধ|আলোচনা|পর্যালোচনা", normalized):
        return "critical_prose_page"
    if line_count <= 8 and centeredish >= max(2, line_count // 2):
        return "section_title_page"
    if long_line_count <= 2 and line_count <= 14:
        return "poem_start_or_short_page"
    if candidates and line_count >= 8:
        return "normal_poem_page"
    return "poem_or_text_page"


def zones_from_text(text: str) -> dict[str, str]:
    lines = text.splitlines()
    nonempty = [line for line in lines if line.strip()]
    return {
        "header_text": "\n".join(nonempty[:4]),
        "body_text": "\n".join(nonempty[4:-4] if len(nonempty) > 8 else nonempty),
        "footer_text": "\n".join(nonempty[-4:]),
    }


def ensure_tools(enable_ocr: bool) -> None:
    required = ["pdfinfo", "pdftoppm"]
    if enable_ocr:
        required.append("tesseract")
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        raise SystemExit(f"Missing required tools: {', '.join(missing)}")


def progress_iter(iterable, **kwargs):
    if tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


def selected_books(book_ids: list[str] | None, primary_only: bool) -> dict[str, dict[str, Any]]:
    books = BOOKS
    if primary_only:
        books = {
            book_id: meta
            for book_id, meta in books.items()
            if meta["source_kind"] == "primary_collection"
        }
    if book_ids:
        unknown = sorted(set(book_ids) - set(BOOKS))
        if unknown:
            raise SystemExit(f"Unknown book id(s): {', '.join(unknown)}")
        books = {book_id: BOOKS[book_id] for book_id in book_ids}
    return books


def build_record(
    *,
    book_id: str,
    meta: dict[str, Any],
    scan_page: int,
    pdf_page_total: int,
    raw_pdftotext: str,
    raw_ocr: str,
    ocr_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = "\n".join(part for part in [raw_pdftotext, raw_ocr] if part)
    candidates = extract_printed_page_candidates(merged)
    features = structural_features(raw_pdftotext, raw_ocr)
    page_type = classify_page(features, merged, candidates)
    normalized = normalize_for_match(merged)

    return {
        "record_id": stable_id(book_id, scan_page),
        "book_id": book_id,
        "collection_bn": meta["collection_bn"],
        "source_kind": meta["source_kind"],
        "pdf_file": meta["pdf_file"],
        "txt_file": meta.get("txt_file"),
        "scan_page": scan_page,
        "pdf_page_total": pdf_page_total,
        "raw_pdftotext": raw_pdftotext,
        "raw_ocr": raw_ocr,
        "ocr_profiles": ocr_profiles,
        "normalized_match_text": normalized,
        "zones": zones_from_text(raw_ocr or raw_pdftotext),
        "layout_features": features,
        "page_type": page_type,
        "printed_page_candidates": candidates,
        "printed_page_fixed": None,
        "printed_page_label_bn": None,
        "printed_page_basis": "unrepaired",
        "sequence_confidence": 0.0,
        "corrected_text_bn": None,
        "correction_status": "raw",
        "flags": [],
    }


def iter_page_numbers(total: int, start: int | None, end: int | None, limit: int | None) -> list[int]:
    first = start or 1
    last = end or total
    pages = [page for page in range(first, last + 1) if 1 <= page <= total]
    if limit is not None:
        pages = pages[:limit]
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="Build page-level OCR corpus JSONL.")
    parser.add_argument("--downloads-dir", default="downloads")
    parser.add_argument("--cache-dir", default=".ocr-cache")
    parser.add_argument("--output", default="metadata_reports/page-corpus.jsonl")
    parser.add_argument("--book-id", action="append", help="Book id to process. Can be repeated.")
    parser.add_argument("--primary-only", action="store_true", help="Process only primary collection scans.")
    parser.add_argument("--start-page", type=int)
    parser.add_argument("--end-page", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--lang", default="ben")
    parser.add_argument("--psm", type=int, default=6)
    parser.add_argument(
        "--profiles",
        default="raw",
        help="Comma-separated OCR image profiles, or 'all'. Choices: raw, gray, threshold, denoise_threshold. Default: raw.",
    )
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bars.")
    parser.add_argument("--skip-tesseract", action="store_true", help="Use only existing pdftotext/archive OCR.")
    parser.add_argument("--force", action="store_true", help="Overwrite output JSONL instead of appending.")
    args = parser.parse_args()

    ensure_tools(enable_ocr=not args.skip_tesseract)

    downloads_dir = Path(args.downloads_dir)
    cache_dir = Path(args.cache_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    books = selected_books(args.book_id, args.primary_only)
    profiles = parse_profiles(args.profiles)
    mode = "w" if args.force else "a"
    records_written = 0

    with output.open(mode, encoding="utf-8") as out:
        for book_id, meta in books.items():
            pdf_path = downloads_dir / meta["pdf_file"]
            txt_path = downloads_dir / meta.get("txt_file", "")
            if not pdf_path.exists():
                print(f"Skipping {book_id}: missing {pdf_path}", file=sys.stderr)
                continue

            raw_pages = split_pages(read_text(txt_path))
            total = page_count(pdf_path)
            page_numbers = iter_page_numbers(total, args.start_page, args.end_page, args.limit)
            profile_label = ",".join(profiles) if not args.skip_tesseract else "pdftotext-only"
            page_iter = page_numbers
            if not args.no_progress:
                page_iter = progress_iter(
                    page_numbers,
                    desc=f"{book_id} [{profile_label}]",
                    unit="page",
                    dynamic_ncols=True,
                    leave=True,
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} pages [{elapsed}<{remaining}, {rate_fmt}]",
                )
            else:
                print(
                    f"Processing {book_id}: {len(page_numbers)} pages, profiles={profile_label}",
                    file=sys.stderr,
                    flush=True,
                )

            for scan_page in page_iter:
                raw_pdftotext = raw_pages[scan_page - 1] if scan_page - 1 < len(raw_pages) else ""
                raw_ocr = ""
                ocr_profiles: list[dict[str, Any]] = []
                if not args.skip_tesseract:
                    image_dir = cache_dir / "images" / book_id
                    raw_image = render_page(pdf_path, scan_page, image_dir, args.dpi)
                    profile_dir = cache_dir / "preprocessed" / book_id / f"{scan_page:04d}"
                    for page_image in preprocess_images(raw_image, profile_dir, profiles):
                        out_base = cache_dir / "ocr" / book_id / f"{scan_page:04d}-{page_image.profile}"
                        text = tesseract_ocr(page_image.path, out_base, args.lang, args.psm)
                        ocr_profiles.append(
                            {
                                "profile": page_image.profile,
                                "text": text,
                                "char_count": len(text),
                            }
                        )
                    raw_ocr = max((p["text"] for p in ocr_profiles), key=len, default="")

                record = build_record(
                    book_id=book_id,
                    meta=meta,
                    scan_page=scan_page,
                    pdf_page_total=total,
                    raw_pdftotext=raw_pdftotext,
                    raw_ocr=raw_ocr,
                    ocr_profiles=ocr_profiles,
                )
                out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                records_written += 1

    print(f"Wrote {records_written} page records to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
