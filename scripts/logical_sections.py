"""Logical collection sections inside shared physical scan files."""

from __future__ import annotations

from typing import Any


LOGICAL_SECTION_OVERRIDES = [
    {
        "physical_book_id": "satti-tarar-timir",
        "start_scan": 62,
        "end_scan": 129,
        "book_id": "jhara-palak",
        "collection_bn": "ঝরা পালক",
        "source_kind": "primary_collection",
    },
    {
        "physical_book_id": "satti-tarar-timir",
        "start_scan": 130,
        "end_scan": 188,
        "book_id": "bela-abela-kalabela-copy",
        "collection_bn": "বেলা অবেলা কালবেলা",
        "source_kind": "auxiliary_collection_section",
    },
]


def apply_logical_section(row: dict[str, Any]) -> dict[str, Any]:
    """Retag shared-PDF rows with their logical collection id."""

    physical_book_id = row.get("physical_book_id") or row.get("book_id")
    scan_page = row.get("scan_page")
    if not physical_book_id or not isinstance(scan_page, int):
        return row

    for override in LOGICAL_SECTION_OVERRIDES:
        if override["physical_book_id"] != physical_book_id:
            continue
        if override["start_scan"] <= scan_page <= override["end_scan"]:
            row.setdefault("physical_book_id", physical_book_id)
            row.setdefault("physical_collection_bn", row.get("collection_bn"))
            row["book_id"] = override["book_id"]
            row["collection_bn"] = override["collection_bn"]
            row["source_kind"] = override["source_kind"]
            return row

    return row
