# OCR-first page corpus plan

## Summary

Build a page-first OCR and alignment pipeline for the Jibanananda Das scans. The smallest authoritative citation unit is a printed book page. The pipeline extracts page images and OCR variants, classifies page structure, repairs printed page-number sequences, and proposes poem spans as reviewable sidecar JSONL. It must not mutate `src/data/poems/*.json` until a later explicit apply workflow is reviewed.

## Operating rules

- Printed book scans outrank online sources for page, edition, and text evidence.
- Raw OCR is never overwritten. Normalized text is for matching only. Corrected text is a separate field with a status.
- Generated artifacts live under `metadata_reports/` and cache directories, not in poem JSON.
- Page classification should use structure first: layout, density, page-number placement, header/footer occupancy, and title/body geometry.
- OCR correction should be dataset-specific and conservative. Repeated OCR mistakes can form equivalence classes, but promotion to corrected text requires a separate review gate.
- The production alignment path must not require AI. Gemini or other vision models may be used only as ad-hoc manual review aids for ambiguous records; their output is not a runtime dependency and should not bypass deterministic gates.

## Pipeline

```text
PDF scan
  |
  +-- pdfinfo / pdftotext
  |
  +-- pdftoppm page rendering
  |
  +-- image preprocessing profiles
  |     raw, grayscale, threshold
  |
  +-- Tesseract Bengali OCR
  |     full page, optional zones/lines
  |
  v
page-corpus.jsonl
  |
  +-- classify_pages.py
  |
  +-- repair_page_sequence.py
  |
  v
page-corpus.repaired.jsonl
  |
  +-- propose_poem_spans.py
  |     deterministic title/line anchors
  |     ordered continuation pages only
  |
  v
poem-span-candidates.jsonl
  |
  +-- apply_poem_metadata.py
  |
  v
gated poem JSON updates
```

## Page corpus contract

Each `metadata_reports/page-corpus.jsonl` row represents one PDF scan page:

- `book_id`, `collection_bn`, `source_kind`, `pdf_file`, `txt_file`, `scan_page`
- `raw_pdftotext`, `raw_ocr`, `ocr_profiles`
- `normalized_match_text`
- `zones`: structural header/body/footer text estimates
- `layout_features`: line counts, density, blank ratio, centered-line ratio, page-number positions
- `page_type`: structural classification
- `printed_page_candidates`
- `printed_page_fixed`, `printed_page_basis`, `sequence_confidence`
- `corrected_text_bn`, `correction_status`
- `flags`

The initial corpus keeps `corrected_text_bn: null` and `correction_status: "raw"`.

## Scripts

- `scripts/ocr_page_corpus.py`
  Builds page-level records from approved PDFs and OCR text. Host Tesseract is the default because `/usr/bin/tesseract` is now healthy; Docker can be used through the runner script when a machine lacks local OCR.

- `scripts/run_ocr_container.sh`
  Optional Docker wrapper for Bengali Tesseract OCR. It writes OCR output into the same cache shape as the host runner.

- `scripts/classify_pages.py`
  Reclassifies page records from structural features only. Pages that begin with a contents marker (`সূচ...` or OCR-damaged `সুচ...`) are treated as front matter even when they have many long table-of-contents lines.

- `scripts/logical_sections.py`
  Retags logical collection sections inside shared physical scan files. For example, `2015.300502.Jibnananda-Dasher.pdf` contains a later `ঝরা পালক` section; rows keep `physical_book_id` for cached images/layout, while `book_id` becomes the collection identity used for matching and citations.

- `scripts/repair_page_sequence.py`
  Repairs printed page numbers using OCR candidates, optional TSV layout candidates, supported scan-to-printed-page offsets, monotonic sequence constraints, and page-type confidence. Offset support prevents a contents-page number from poisoning later poem pages.

- `scripts/propose_poem_spans.py`
  Proposes poem-to-page spans using the repaired page corpus and current poem JSON. It emits sidecar candidates only. Accepted candidates require deterministic title/line anchors and a repaired printed page range; a match without printed book page numbers remains a manual-review candidate because the website citation must name printed pages rather than PDF scan pages. The current algorithm is deterministic:

  - normalize Bengali text with known OCR equivalence classes;
  - optionally apply a generated OCR substitution map for matching only;
  - optionally search logical copy-section aliases for the same source
    collection, such as `mahaprithibi-appendix-copy` for `মহাপৃথিবী`, via the
    explicit `--include-logical-aliases` review flag;
  - score candidate pages by title, first/last line, body-token overlap, and repaired printed-page availability;
  - derive the span from clustered title/line anchors rather than broad adjacent token overlap;
  - reject title-only anchors, which are unsafe for short titles like `তুমি`;
  - include adjacent continuation pages only when matched line indexes continue in poem order;
  - leave ambiguous, weak, or collection-conflicting records for manual review.

- `scripts/extract_page_layout.py`
  Extracts Tesseract TSV geometry from cached page images. This adds structure-first page-number evidence and body/header/footer placement summaries without overwriting raw OCR.

- `scripts/apply_poem_metadata.py`
  Applies only gated span candidates to poem JSON. A row must be an accepted candidate, have printed page start/end, include deterministic span-anchor evidence, and either match the poem's known collection or fill `সংকলন অজানা`. It does not mark poems as text-verified. Legacy broad-token candidate reports require the explicit `--allow-legacy-candidates` override.
  Logical copy-section IDs are mapped back to the canonical collection metadata
  before writing `book_sources`, so a `mahaprithibi-appendix-copy` candidate is
  cited as `মহাপৃথিবী`. Alias candidates fill missing citations only during
  bulk application; they do not overwrite an existing primary citation for the
  same collection.

- `scripts/ocr_lexicon_audit.py`
  Builds a Bengali token lexicon from the current Jibanananda poem JSON and
  compares OCR page tokens against it. The output is a sidecar suspicion report
  with repeated unknown OCR forms, page contexts, and likely corrections from
  OCR-equivalence keys or edit similarity. It also exports a conservative
  sidecar substitution map for `propose_poem_spans.py --ocr-substitutions`.
  This is a matching-only gate: it can prioritize pages, help grow OCR
  equivalence classes, and recover page-span evidence hidden by repeated OCR
  errors, but it must not rewrite poem text or page corpus rows without separate
  review.

All long-running page and poem loops use `tqdm` progress bars when run through `uv`.

## Current gated application

The first site-facing application used `metadata_reports/poem-span-candidates.full.layout.normal.jsonl` and wrote printed book citations for 114 Jibanananda poem records:

- 58 records moved out of `সংকলন অজানা` into a matched primary collection.
- 56 records kept their known collection and gained printed page citations.
- 239 rows were not accepted candidates.
- 11 accepted candidates were skipped because printed page start/end was missing.
- 25 rows were skipped because the candidate book contradicted a known collection assignment.

A second pass improved printed page repair from 577/728 fixed page records to 619/728 by using supported page offsets. It unlocked 9 more gated poem updates:

- 2 records moved out of `সংকলন অজানা`.
- 7 records kept their known collection and gained printed page citations.

Current cumulative site-facing metadata state:

- 276 of 389 Jibanananda records have printed book citations.
- 267 of 375 public/non-duplicate Jibanananda records have printed book citations.
- 96 public/non-duplicate records still have `সংকলন অজানা` as the collection.
- 98 public/non-duplicate records still lack `source_year`.
- Public counts exclude exact or partial duplicate import rows, including the
  parenthesized `সূর্য নক্ষত্র নারী` fragments and the alternate `সেই দিন এই
  মাঠ` import whose cited `রূপসী বাংলা` counterpart remains listed, plus the
  stanza-only `চারিদিকে শান্ত বাতি` fragment.

Remaining `সংকলন অজানা` poems stay in the metadata backlog until a manual or stronger automated pass resolves them.

A reviewed appendix pass added four `মহাপৃথিবী` citations from the `সংযোজন`
section in the shared scan: `মনোকণিকা` pages 173-174, `সুবিনয় মুস্তফী` page
175, `অনুপম ত্রিবেদী` pages 175-176, and `একটি নক্ষত্র আসে` page 176. The
evidence is the appendix contents list plus the visible page 173 anchor and
contiguous page order through 176. The imported online source marker
`(মহাপৃথিবী কাব্যগ্রন্থ)` was removed from the two affected poem bodies.

A reviewed logical-alias pass corrected two stale `সাতটি তারার তিমির`
assignments to `বেলা অবেলা কালবেলা`: `ইতিহাসযান` uses the logical copy-section
evidence on pages 164-168, while `হে হৃদয়` uses the stronger primary-scan
evidence on pages 67-68. The pass also marked
`চারিদিকে শান্ত বাতি` as a public duplicate fragment because the page corpus
shows it is a stanza inside the already imported `সেইদিন এই মাঠ...` poem.

The next deterministic pass replaces broad adjacent-token span expansion with line-anchor clustering. In the current local report it keeps 146 accepted candidates, rejects or defers 243 records, reduces accepted spans longer than four pages from 29 to 1, and restores short continuation pages only where line indexes continue in order. This pass is intended to correct over-wide printed-page citations before further expansion.

## Current lexicon audit

The first non-mutating dictionary-style pass is implemented in
`scripts/ocr_lexicon_audit.py`. A run with
`uv run python scripts/ocr_lexicon_audit.py --no-progress --top 500` built a
13,053-token Jibanananda poem lexicon from the current JSON files, scanned 626
poem/text pages from `page-corpus.full.repaired.layout.jsonl`, and exported a
matching-only substitution map.

The report is written to the ignored sidecar file
`metadata_reports/ocr-lexicon-audit.current.json`; the substitution map is
written to `metadata_reports/ocr-lexicon-substitutions.current.json`. It surfaces
repeated OCR forms with suggested corrections, for example `আঁম` -> `আমি`,
`যাঁদ` -> `যদি`, `পাঁথবীর` -> `পৃথিবীর`, and `শাঁশরের` -> `শিশিরের`. Feeding
that map into span proposal changed the report from 144 to 160 accepted
candidates and reduced `no_candidate` records from 69 to 59. Against the current
site data, the only gated JSON change was `সুচেতনা`: its printed citation moves
from `বনলতা সেন`, page 28, to pages 27-28 after the page-27 OCR is normalized
enough to reveal the opening stanza. This is not a text-correction gate and must
not automatically rewrite poem bodies.

## Test plan

- `uv run python -m py_compile scripts/ocr_page_corpus.py scripts/classify_pages.py scripts/repair_page_sequence.py scripts/propose_poem_spans.py scripts/extract_page_layout.py scripts/apply_poem_metadata.py scripts/ocr_lexicon_audit.py`
- `scripts/*.py --help` should print CLI usage without requiring OCR execution.
- Smoke run:
  - Generate a small page corpus for 2-3 pages from one book.
  - Classify and repair the small corpus.
  - Propose spans for a small poem subset.
  - Confirm generated JSONL parses and no poem JSON files changed.
- Site regression: `bun run build` after script/doc additions.

## Deferred work

- Human-reviewed text correction and stanza recovery.
- Affiliate/publisher purchase-link population.
- Full metadata sprint for collection mapping, composition dates, duplicate titles, and proofing status.
