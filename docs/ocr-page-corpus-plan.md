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

## Posterior evidence model

The deterministic pipeline should be read as a factor graph over page, text,
source, and citation variables. Every transformation emits observations that
raise or lower confidence; no single OCR string or fuzzy match is treated as
ground truth by itself.

```text
                 scan/image fidelity
                         |
                         v
PDF page -> preprocessing profile -> OCR stream -> normalized OCR text
   |               |                 |              |
   |               |                 |              v
   |               |                 +------> OCR confusion classes
   |               v
   |        layout geometry ----------------> page type / zones
   |                                             |
   v                                             v
printed-page sequence <---------------- page-number candidates
   |
   v
candidate poem span <--- title/line anchors <--- current poem text
   |                    |       |
   |                    |       +------> dictionary / equivalence-class evidence
   |                    v
   |              ordered contiguous-region evidence
   |
   v
source-edition prior + book-corpus coverage + reviewed exclusions
   |
   v
posterior-style citation triage
   |
   +-- apply only through explicit deterministic gates
   +-- otherwise remain as review sidecars
```

This is not yet a calibrated probabilistic model. The current implementation is
a transparent factor ledger: OCR fidelity, geometry, page-sequence repair,
source priors, candidate competition, text anchors, and human review each become
named evidence factors. The factor report records the base probability and the
posterior after each transformation stage, so a row can be inspected as a
sequence of updates rather than a single fuzzy score. Later Bayesian calibration
can reuse those same factors without changing the production rule that poem JSON
is updated only by explicit apply gates.

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
  Reclassifies page records from structural features only. Pages that begin with a contents marker (`সূচ...` or OCR-damaged `সুচ...`) are treated as front matter even when they have many long table-of-contents lines. Pages with many short Bengali title/page-number rows are also treated as front matter, so contents digits cannot poison poem-page sequence repair.

- `scripts/logical_sections.py`
  Retags logical collection sections inside shared physical scan files. For example, `2015.300502.Jibnananda-Dasher.pdf` contains a later `ঝরা পালক` section; rows keep `physical_book_id` for cached images/layout, while `book_id` becomes the collection identity used for matching and citations.

- `scripts/repair_page_sequence.py`
  Repairs printed page numbers using OCR candidates, optional TSV layout candidates, supported scan-to-printed-page offsets, monotonic sequence constraints, and page-type confidence. Offset support prevents a contents-page number from poisoning later poem pages.
  When OCR creates a sparse false offset, such as reading `১২/১৪/১৬` as
  `৯২/৯৪/৯৬`, a dominant-offset rule keeps the coherent book sequence and
  rejects the weaker parallel offset.
  It also allows a short trusted tail after two visible sequential anchors; this
  handles cases where the final poem page in a section has no readable printed
  page number but directly follows visible pages.
  It also allows a short leading run before the first visible sequential
  anchors, but only when the leading window contains a trusted text/poem page.
  This covers unnumbered opening poems such as `রূপসী বাংলা` page 9 without
  turning blank-only front matter into citation pages.
  For very short appendix sections with only one visible anchor, it can also
  infer a short footer-backed tail when the next pages visibly carry the
  expected page numbers in footer-only OCR. This handles `মহাপৃথিবী`
  সংযোজন pages 174-176, including the noisy `১৭৫৬` footer read for page 175,
  without treating Bengali title/date lines as page numbers.

- `scripts/propose_poem_spans.py`
  Proposes poem-to-page spans using the repaired page corpus and current poem JSON. It emits sidecar candidates only. Accepted candidates require deterministic title/line anchors and a repaired printed page range; a match without printed book page numbers remains a manual-review candidate because the website citation must name printed pages rather than PDF scan pages. The current algorithm is deterministic:

  - normalize Bengali text with known OCR equivalence classes;
  - optionally apply a generated OCR substitution map for matching only;
  - optionally search logical copy-section aliases for the same source
    collection, such as `mahaprithibi-appendix-copy` for `মহাপৃথিবী`, via the
    explicit `--include-logical-aliases` review flag;
  - score candidate pages by title, first/last line, body-token overlap, and repaired printed-page availability;
  - treat title evidence as heading-aware: exact normalized title lines count as `title_match`, while title phrases embedded in poem body text are downgraded to `title_phrase_match`;
  - derive the span from clustered title/line anchors rather than broad adjacent token overlap;
  - reject title-only anchors, which are unsafe for short titles like `তুমি`;
  - trim sparse pre-title anchor pages only when they are repeated-line/refrain
    matches with no opening-line evidence before a title/opening-line anchor;
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
  same collection. Existing printed primary page ranges are protected by
  default; replacing a page range requires the explicit
  `--allow-existing-page-overwrite` flag after review against the printed
  source. Known collection conflicts are not overwritten by default. They can
  be updated only with an explicit conflict flag: the accepted-candidate gate
  requires deterministic all-books evidence, repaired printed pages, high body
  coverage, title or opening-line evidence, exact line anchors, and a clear
  runner-up gap before changing `source_edition`, `source_year`, `phase_id`, and
  the primary printed-page citation together. A separate outside-range repair
  gate requires `--page-corpus`, verifies that the replacement printed pages
  exist in the exact candidate scan, and only rewrites citations whose current
  printed page range is outside the repaired corpus range for the named source.
  Logical alias/copy-section evidence is deliberately excluded from gates that
  force a source-edition change; aliases may only repair same-book page
  citations through the explicit alias-repair path.
  The default apply scope skips duplicate-hidden poem imports listed in
  `src/lib/content.ts`; use
  `--include-duplicates` only for a deliberate duplicate-cleanup pass.

- `scripts/report_metadata_gaps.py`
  Builds a review-only Markdown/JSON inventory of public Jibanananda records
  that still lack printed book page citations. It joins the current poem JSON
  with the current regenerated span-candidate report by default, excludes hidden
  duplicate imports, and ranks gaps into buckets such as `manual_collection_review`,
  `needs_printed_page_sequence`, `weak_text_anchor`,
  `conflicting_embedded_source_marker`, `token_or_title_only_candidate`, and
  `no_candidate`. This is
  the handoff list for printed-source review; it does not apply metadata. It
  also reads `src/data/metadata-review-exclusions.json`, a committed audit trail
  of candidate-specific false positives. Reviewed exclusions remain in the
  missing-citation count, but are moved to `reviewed_*` buckets so the next
  review pass focuses on unresolved evidence instead of rechecking disproved
  title collisions or weak one-line overlaps. By default it also checks each
  known-source gap against `metadata_reports/page-corpus.full.repaired.layout.jsonl`
  and reports whether the source book is unscanned, weakly supported by OCR
  tokens, or supported by title/line evidence. Pass an empty `--candidates ""`
  or `--page-corpus ""` only when intentionally generating a reduced report.
  The report also groups known-source gaps into source-coverage blockers, which
  is the handoff list for adding missing scans or performing direct printed-book
  review. It also surfaces explicit embedded source-marker conflicts, so a
  source note imported into the poem body cannot stay hidden behind a generic
  weak-candidate row.

- `scripts/citation_factor_model.py`
  Builds a review-only posterior-style factor ledger for the unresolved
  printed-page gaps emitted by `report_metadata_gaps.py`. It scores each row
  from named pipeline factors: source priors, page-sequence repair, candidate
  span basis, exact/title/opening-line evidence, runner-up separation,
  source-corpus coverage, and reviewed exclusions. Its `posterior_like` score is
  heuristic triage, not an apply gate. Each row also includes `stage_updates`,
  which show the posterior before and after every transformation stage, plus
  aggregate stage delta totals in the report summary. The output separates rows
  that need better extraction, source identification, source-corpus review,
  stronger text anchors, or continued exclusion.

- `scripts/embedded_source_audit.py`
  Audits explicit source markers that were imported into poem bodies, such as a
  trailing `(অগ্রন্থিত কবিতা)` or `#অগ্রন্থিত কবিতা`. The matcher is deliberately
  narrow so ordinary poem text mentions of a collection title do not become
  metadata. With `--apply`, it may classify `সংকলন অজানা` rows and remove the
  source marker from the body text, but it never writes printed-page citations.
  Rows where an embedded marker conflicts with an existing source edition are
  reported as review debt and left unchanged.

- `scripts/source_url_marker_audit.py`
  Audits explicit collection labels on the existing online source URLs. This is
  a secondary-source classifier, not a printed-book citation source: with
  `--apply`, it may fill `source_edition`, `source_year`, `phase_id`, and a
  canonical redirected source URL when the page text contains an exact label
  like `কাব্যগ্রন্থ - আলোপৃথিবী`. It never writes `book_sources` or page
  numbers. Rows without an exact marker remain unclassified even if OCR
  candidates have title or token overlap in scanned books.

- `scripts/phrase_window_audit.py`
  Builds a review-only exact phrase-window report for remaining poems by
  matching normalized 4-6 token body phrases against trusted OCR page records.
  It is a stricter second opinion for cases where full-line anchors fail
  because OCR or imported poem text has different line breaks. Its output is
  still evidence for manual review, not an automatic apply source: a phrase hit
  can identify an overlapping passage inside a different printed poem, or a
  page where the printed title differs from the imported title. It reads the
  same reviewed-exclusion file as the metadata-gap reporter, so checked
  false-positive phrase hits do not keep reappearing in the review queue.

- `scripts/toc_index_audit.py`
  Builds a review-only table-of-contents title/page report from existing OCR
  sidecars. It parses TOC title/page pairs, follows repaired printed page
  numbers back to the logical collection section inside shared physical scans,
  and compares entries against remaining poem-title gaps with OCR-equivalence
  normalization. It also compares TOC entries against the first substantial
  poem-body lines, because some printed contents use opening lines while the
  imported JSON title uses a later/common short title. It guards duplicate local
  titles: title-only evidence cannot become apply-grade when another public poem
  with the same title already has stronger page evidence. This is a possible
  future apply source only if a row has a unique title or strong known-source
  opening-line match, verified page sequence, and no known-collection conflict.

- `scripts/fuzzy_line_audit.py`
  Builds a review-only fuzzy line report for remaining printed-page gaps after
  exact line and phrase-window matching are exhausted. Its vector prefilter is
  deliberately local-region aware: the page-wide exact/class shingle overlap is
  only a cheap recall gate and is never citation evidence. Candidate scoring
  compares representative poem lines against sparse hashed embeddings for
  contiguous OCR windows only. Windows are source-local: a window can span
  neighboring `ocr` lines, neighboring `pdftotext` lines, or neighboring
  profile lines, but it cannot cross from one extraction stream into another.
  Each embedding has two feature channels: exact contiguous character shingles
  at full weight, and OCR class-normalized contiguous character shingles at half
  weight. Longer contiguous shingles (`3,5,8` by default) get higher weights.
  Region embeddings are built lazily from per-page inverted indexes, so
  expensive vector dots do not run over every OCR window in the corpus. When
  NumPy is available, the same page-local sparse postings are scored with
  vectorized dot accumulation; this is an acceleration of the contiguous-region
  matcher, not a page-wide embedding shortcut. The detailed pass still requires
  each accepted poem line to match one local OCR region, then verifies that
  local region with the longest contiguous exact and OCR-class character runs.
  Exact contiguous runs count fully; class-only contiguous runs add half credit.
  It reports both the longest consecutive run of matched poem lines and a
  source-aware ordered run of local OCR regions. The vector prefilter keeps the
  top `--vector-top-regions-per-line` local placements per representative line
  for the ordered-region check, while still counting each poem line only once
  for page score. A strong fuzzy review now requires the ordered region run to
  meet the configured threshold
  (`--min-ordered-region-run`, default `3`); same-page consecutive matches must
  remain in the same extraction source and within `--max-ordered-region-gap`
  normalized OCR lines. Candidate windows are capped to short printed-page
  spans. This report is not an apply source; fuzzy-only candidates must still be
  checked against printed-page evidence before writing poem metadata.

- `scripts/ordered_region_audit.py`
  Builds a review-only ordered-token report for the remaining printed-page
  gaps. It compares poem lines against local OCR line windows using token order,
  exact/class-normalized token overlap, longest common subsequence, and longest
  contiguous token runs. This is a stricter deterministic second opinion for
  whether fuzzy-looking matches preserve poem order inside the page. The current
  default gate found no rows; a permissive run
  (`--min-line-score 0.62 --min-candidate-lines 2`) found only three
  `weak_ordered_review` rows, all with ordered runs of one and no citation-grade
  page span. Its output is a triage sidecar only and must not be applied without
  exact ordered anchors and printed-page verification.

- `scripts/citation_consistency_audit.py`
  Audits existing primary printed-page citations against the repaired OCR
  corpus. It checks whether the cited printed page exists in the exact source
  scan or a logical alias, then looks for title, opening-line, exact-line, and
  body-token evidence on that cited page range. It is review-only and does not
  mutate poem JSON. It accepts repeatable `--extra-page-corpus` inputs so
  auxiliary book sidecars can be layered into review without changing the
  default corpus file. It distinguishes a cited page that is missing from an
  otherwise loaded source corpus (`missing_page_rows`) from a source book that
  is absent from the loaded corpus entirely (`missing_book_corpus`).

- `scripts/citation_repair_audit.py`
  Builds a stricter review-only report for repairing existing printed-page
  citations. It joins the citation consistency report with current span
  candidates, but only promotes a repair as automatic when the candidate comes
  from the same exact source-book ID as the existing citation. Logical
  copy/appendix sections, such as `rupasi-bangla-copy`, are reported as
  alternate page evidence rather than overwrite candidates, because their page
  numbers can follow a different physical book convention than the linked poem
  page source. This protects the requirement that citations use the printed page
  number in the book being linked, not merely any scan where the poem appears.

- `scripts/composition_date_audit.py`
  Builds a review-only report for possible composition date/place signatures
  near already-cited printed page spans. It only inspects pages already cited in
  `book_sources`, searches nearby OCR lines after matched poem endings and page
  tails, and requires a stricter signature pattern: a Bengali month with a
  digit, or a year paired with a known location word. Plain month words inside
  poem lines and year-only memorial/context notes are not treated as composition
  dates. The current strict run found no date candidates: 270 cited poems had no
  date signature candidate, and 103 poems still lack a primary printed-page
  citation to inspect.

- `scripts/ocr_lexicon_audit.py`
  Builds a Bengali token lexicon from the current Jibanananda poem JSON and
  compares OCR page tokens against it. The output is a sidecar suspicion report
  with repeated unknown OCR forms, page contexts, and likely corrections from
  OCR-equivalence keys or edit similarity. It also exports a conservative
  sidecar substitution map for `propose_poem_spans.py --ocr-substitutions`.
  It also writes `metadata_reports/poem-text-quality.current.json`, a
  review-only pass over the live poem bodies. That report distinguishes
  standalone Bengali digit lines, which are usually poem section markers, from
  inline digits or replacement characters that need review. Site poem pages now
  render those standalone digit stanzas as quiet section markers. This is still
  a matching/review gate only: it can prioritize pages, help grow OCR
  equivalence classes, and recover page-span evidence hidden by repeated OCR
  errors, but it must not rewrite poem text or page corpus rows without separate
  source review.

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

- 280 of 389 Jibanananda records have printed book citations.
- 267 of 369 public/non-duplicate Jibanananda records have printed book citations.
- 102 public/non-duplicate Jibanananda records still lack printed book citations.
- 66 public/non-duplicate records still have `সংকলন অজানা` as the collection.
- 73 public/non-duplicate records still lack `source_year`.
- Public counts exclude exact or partial duplicate import rows, including the
  parenthesized `সূর্য নক্ষত্র নারী` fragments and the alternate `সেই দিন এই
  মাঠ` import whose cited `রূপসী বাংলা` counterpart remains listed, plus the
  stanza-only `চারিদিকে শান্ত বাতি` fragment and the composite dialogue import
  `আকাশে সাতটি তাঁরা`.

Remaining `সংকলন অজানা` poems stay in the metadata backlog until a manual or stronger automated pass resolves them.

The enhanced gap report now separates known-source failures. In the current
sidecar run, 27 known-source gaps cite editions not mapped to a current OCR book
corpus (`আলোপৃথিবী`, `অগ্রন্থিত কবিতা`, or `অপ্রকাশিত কবিতা`). Nine known-source
gaps do target scanned collections, but seven have only weak same-source OCR
support and two have no current scan support; none has title or exact-line
evidence strong enough for automatic page citation.

An embedded-source marker pass classified three unknown rows as
`অগ্রন্থিত কবিতা`: `কোহিনূর`, `বর্ষ-আবাহন`, and `ভোর হয়`. The source-note
markers were removed from the poem body text. This pass intentionally did not
add printed-page citations, because the markers identify collection status but
do not prove a book page. The same audit surfaced one unresolved conflict:
`তবু` is currently filed under `শ্রেষ্ঠ কবিতা`, while the imported body ends
with a `সাতটি তারার তিমির কাব্যগ্রন্থ` marker. That marker is not used to
rewrite the poem's source edition without printed-source evidence; it remains a
separate original-collection/text-cleanup review item.

A secondary online-source marker pass classified three more unknown rows as
`আলোপৃথিবী`: `কার্তিক-অঘ্রাণ ১৯৪৬`, `জার্মানীর রাত্রিপথেঃ ১৯৪৫`, and
`ভোর ও ছয়টি বমারঃ ১৯৪২`. The existing Bangla-Kobita source pages explicitly
show `কাব্যগ্রন্থ - আলোপৃথিবী`; this was used only for source edition, source
year, and phase. No printed-page citation was added, because the linked online
pages do not prove a book page number and the `আলোপৃথিবী` OCR corpus is still
absent. The remaining title-date/source-year gap is `কার্ত্তিকের ভোর- ১৩৫০`,
whose online page did not show a collection marker in the fetched HTML.

A follow-up auxiliary pass generated a scratch `শ্রেষ্ঠ কবিতা` corpus from the
approved project downloads using `pdftotext` only, then repaired its printed
page sequence after the contents-page and dominant-offset fixes. Three
same-source accepted candidates were applied: `তবু` on printed pages 112-114,
`পৃথিবীতে` on page 114, and `লোকেন বোসের জর্নাল` on pages 119-120. The full
citation audit was run against a combined current corpus plus the scratch
`srestha-kabita` rows; all three new citations were supported. The generated
JSONL corpus remains ignored and should be regenerated when the OCR sidecars are
rebuilt.

A manual evidence pass over the strongest remaining line-anchor candidates added
`ভিখিরী` to the `বনলতা সেন` appendix on printed pages 171-172 and `হাঁস` to
`সাতটি তারার তিমির` printed page 21. The same pass identified
`স্বাতীতারা` as a duplicate import of the already-cited `পটভূমির` record and
hid it from public routes.

A follow-up schema normalization added the missing `primary` role to three
older printed-page citations (`অঘ্রাণ`, `অনন্ত জীবন যদি পাই আমি`, and
`অনির্বাণ`) so review reports and overwrite guards count them as existing
book-backed sources.

A later cleanup rechecked those older Gemini-backed citations against the
repaired corpus. `অঘ্রাণ` was corrected from printed page 95 to 91 in
`ধূসর পাণ্ডুলিপি`; the printed page visibly carries the damaged OCR title and
body. The stale citations for `অনন্ত জীবন যদি পাই আমি` (`সাতটি তারার তিমির`
p.136, outside the scanned p.6-62 corpus) and `অনির্বাণ` (`বেলা অবেলা
কালবেলা` p.67, which belongs to `হে হৃদয়`) were removed rather than displayed
as book-backed evidence. The same pass removed the imported
`(সাতটি তারার তিমির কাব্যগ্রন্থ)` source marker from the body of `তবু` and
downgraded unsupported `verified: true` flags that were not line-by-line
proofreading evidence. After this cleanup the default citation audit has no
`outside_corpus_range` or `weak_current_citation` rows; the remaining
`missing_book_corpus` rows are the three `শ্রেষ্ঠ কবিতা` citations whose
auxiliary corpus is not present in the default sidecar file.

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

A reviewed span-tightening pass fixed `মেঠো চাঁদ` in `ধূসর পাণ্ডুলিপি` from
pages 15-17 to pages 16-17. Page 15 is the end of the previous poem; pages 16-17
carry the visible `মাঠের গল্প` / `মেঠো চাঁদ` start and continuation. The
generator now guards this case by trimming only sparse repeated-line anchors
that precede a title/opening-line page, while keeping dense OCR-damaged starts
such as `ডাকিয়া কহিল মোরে রাজার দুলাল` on their first printed page.

A duplicate-data pass hid `আকাশে সাতটি তাঁরা` (`jibanananda-029`) from public
lists. Its imported body is a composite `ছেলে`/`মেয়ে` dialogue made from several
poem fragments, while the clean `রূপসী বাংলা` poem
`আকাশে সাতটি তারা যখন উঠেছে ফুটে` (`jibanananda-030`) remains public with the
printed page 15 citation.

A later duplicate-data pass hid four more near-identical public imports:
`কোথাও দেখি নি` (`jibanananda-103`), `বিড়াল` (`jibanananda-235`),
`যতোদিন পৃথিবীতে` (`jibanananda-282`), and `সে কামনা নিয়ে`
(`jibanananda-360`). Their canonical public counterparts remain
`কোথাও দেখিনি আহা এমন বিজন ঘাস` (`jibanananda-104`), `বেড়াল`
(`jibanananda-241`), `যতদিন পৃথিবীতে` (`jibanananda-278`), and
`যে কামনা নিয়ে` (`jibanananda-285`). The hidden rows stay in the JSON corpus
for audit comparisons, but no longer create duplicate public poem pages.

A trailing page-sequence pass added a printed citation for `তোমাকে`
(`jibanananda-158`). In the `বনলতা সেন` সংযোজন section, visible page anchors
169 and 171 plus the intervening sequence infer the following page as printed
172. The page contains the poem title, high body coverage, nine line matches,
and three exact line anchors. The poem remains classified as
`অপ্রকাশিত কবিতা`; only the printed book-source citation was added.

A review-exclusion pass added `src/data/metadata-review-exclusions.json` and
taught the gap reporter to demote candidate-specific false positives. Current
reviewed exclusions include the duplicate-title `সুদর্শনা` collision, weak
one-line/page overlaps for `ঊনিশশো চৌত্রিশের`, `দিনরাত্রি`,
`তোমাকে ভালোবেসে`, `মাঝে মাঝে`, and `সারা দিন ট্রাম-বাস`, front-matter or
publisher-page false positives for `বিপাশা`, `রবীন্দ্রনাথ`, `রাত্রি ও ভোর`,
and `সন্ধ্যা হয়ে আসে`, plus the current scan-corpus absence of
`অদ্ভুত আঁধার এক`. A later phrase-window review added two more rejected phrase
overlaps: `কত দিন ঘাসে আর মাঠে` against a `পঁচিশ বছর পরে` passage in
`ধূসর পাণ্ডুলিপি`, and `উদয়াস্ত` against `বিভিন্ন কোরাস` in
`সাতটি তারার তিমির`. These rows are still metadata gaps; the sidecar only
records that the listed candidate evidence is insufficient and must not be
applied without stronger printed-page proof. After this pass, the all-books gap
report has no unresolved candidate with line-anchor evidence, and the
phrase-window queue has no unreviewed matches; the remaining unresolved rows
are mostly `token_or_title_only_candidate` matches or no-candidate records, plus
the explicit `তবু` embedded-source conflict that now has its own review bucket.
The TOC-only `উদয়াস্ত` hit against `মহাপৃথিবী` copy page 127 is also now a
reviewed exclusion: the same printed page matches the distinct `jibanananda-055`
body with title, opening line, and line-anchor evidence, so it must not be used
to classify `jibanananda-054`.

A later review pass rejected the four remaining higher-posterior
token/body-overlap candidates: `ঘাটশিলা—ঘটশিলা`, `সমুদ্রের জলে আমি দেহ ধুয়ে`,
`অনির্বাণ`, and `সোনালী ডানার শঙ্খচিল`. Their candidate pages have repaired
printed page numbers but no title, opening-line, exact-line, or contiguous body
anchors for the imported poem bodies. The factor report now has no
`needs_stronger_text_anchors` rows; the unresolved queue is split between
better text extraction, collection identification, source-corpus review, and
reviewed exclusions.

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

The same audit now writes a poem-body quality report. A current run finds 50
digit-related lines: 42 are standalone Bengali section-number stanzas and 8 are
inline digits that should be reviewed as possible source notes, footnote
markers, or OCR artifacts. The dictionary-style token candidates remain noisy:
the equivalence keys find real likely typos, but they also collapse valid
poetic words such as `কোণে`, `ভোলো`, `ভেলা`, and `শোণ` into common alternatives.
Use this report to queue manual/printed-source checks, not bulk text rewrites.

The initial page-wide fuzzy-line audit over the remaining 103 public
printed-page gaps ran in about 25 seconds, but it generated a broad
false-positive queue: many high-scoring rows were fuzzy-only and drew character
shingles from unrelated regions of the same page. The region-aware embedding
pass uses narrower review defaults (`--vector-top-pages 8`,
`--vector-page-prefilter-multiplier 2`, `--vector-max-lines 6`) and completed a
current full run in about 110 seconds including page-window setup. It found one
diagnostic-only weak row (`তোমায় আমি`), with no exact lines and an ordered
region run of one. Treat this as negative evidence for automatic fuzzy
application: the remaining gaps need better OCR/source extraction or manual
printed-source review rather than looser fuzzy citation writes.

The fuzzy verifier now treats page-wide overlap as recall-only and local
contiguity as the evidence gate. Its vector features are still hashable and
cacheable, but every scored feature comes from a contiguous character shingle
inside a bounded, source-local OCR line window, with a parallel
OCR-equivalence-class channel. The follow-up line score measures the longest
contiguous substring inside that same window, and the candidate status gate
requires consecutive poem lines to advance through nearby OCR windows in source
order. The vector prefilter preserves multiple nearby local placements per
representative line for that ordered-run calculation; it does not collapse the
page into a single bag-of-shingles decision. Repeated OCR confusions can improve
a weak partial match without letting unrelated tokens from different page
regions or extraction streams combine into a citation.

Any future NumPy/SIMD acceleration must preserve those same boundaries. The
unit of comparison is a bounded OCR region, not a whole page; features are
contiguous exact character shingles plus half-weight OCR-class shingles; and a
candidate must still show consecutive poem lines in nearby source-local regions
before it can rise above weak review.

The ordered-region audit confirms the same result from a stricter angle. Its
default gate found zero candidates. A permissive diagnostic run found three weak
rows (`কত দিন ঘাসে আর মাঠে`, `গল্পে আমি পড়িয়াছি কাঞ্চী কাশী বিদিশার কথা`,
and `সে`), but each had only one ordered run and matched common local phrases
rather than a stable poem span. No poem JSON should be updated from these rows.

The TOC index audit parsed 233 title/page entries from the repaired corpus,
including continuation TOC pages and logical sections in the shared
`banalata-sen` scan. Its normal-threshold `উদয়াস্ত` hit is now filtered through
`src/data/metadata-review-exclusions.json`: `jibanananda-055` has the stronger
body match for that printed page, while `jibanananda-054` is a distinct imported
body with only title-level overlap. The current gap report has zero unreviewed
TOC blockers and one reviewed TOC exclusion. A lower diagnostic threshold still
produces only weak title matches and one missing-page sequence row. No poem JSON
should be updated from the current TOC report.

The main metadata gap report now separates source-year, primary printed
book-year, and composition-date debt from page-citation debt. A later
source-URL marker pass found 13 more explicit `কাব্যগ্রন্থ - আলোপৃথিবী`
labels on existing Bangla-Kobita source pages and used them only to fill
source edition/year/phase. The current report has 102 missing printed-page
citations, 102 missing primary printed book years, 73 missing editorial source
years, and 369 missing composition dates. The primary printed book-year count
matches the missing printed-page count, which means every current primary
printed-page citation already carries a cited-book publication year; the
remaining book-year debt is really page-citation debt. Since the strict
composition-date audit found no authorial date/place signatures in the
currently cited OCR spans, composition dates should not be filled until stronger
printed-source evidence is added or manually reviewed.

The apply script dry-run now uses the same duplicate-hidden scope as the public
site by default. With all review gates enabled against the current regenerated
span candidates, it reports `changed: []`; the only previously writable row was
already a hidden duplicate import. This is negative evidence for further
automatic public metadata writes from the main regenerated sidecar, but not a
claim that every auxiliary review sidecar is exhausted.

A later conflict-gated all-books pass corrected three stale source assignments:
`বিস্ময়`, `যদিও দিন`, and `সারাৎসার` moved from `সাতটি তারার তিমির` to
`বেলা অবেলা কালবেলা`, with printed pages 40-41, 63-64, and 55 respectively.
The gate required accepted deterministic span candidates, repaired printed
pages, high body coverage, title or opening-line evidence, exact line anchors,
and a runner-up gap of at least 4. A repeat dry run with
`--allow-conflict-accepted-candidates` is now idempotent.

A later corpus-backed outside-range repair pass regenerated the lexicon
all-books sidecar from `metadata_reports/page-corpus.full.repaired.layout.jsonl`
before applying. It corrected four citations whose existing page ranges were
outside the named source corpus: `দেশ কাল সন্ততি`, `নারীসবিতা`, and
`সূর্য রাত্রি নক্ষত্র` moved from stale `সাতটি তারার তিমির` citations to
`বেলা অবেলা কালবেলা`, and `মাঘসংক্রান্তির রাতে` stayed in
`বেলা অবেলা কালবেলা` but moved from printed page 11 to printed page 135. The
new gate rejected stale sidecar rows whose printed pages no longer exist in the
current corpus, including `তোমাকে` page 172.

The citation consistency audit distinguishes absent scan coverage from
potentially wrong citations. In the current default sidecar run, 264 of 267
existing citations have current corpus support: 234 by title/opening/exact-line
evidence and 30 by broad token coverage. The remaining three are not weak
in-corpus citations; they are `missing_book_corpus` rows for `শ্রেষ্ঠ কবিতা`.
Those rows need an auxiliary `srestha-kabita` page corpus to be regenerated and
passed with `--extra-page-corpus` before the default audit can recheck them.
The project should not read or regenerate from the local `downloads/` PDFs for
that auxiliary corpus until explicitly permitted.

A scratch regeneration of `repair_page_sequence.py` with the leading-sequence
guard and current layout sidecar inferred only `রূপসী বাংলা` scan pages 13-14
as printed pages 9-10. Against that scratch corpus, `সেইদিন এই মাঠ` page 9
became `supported_token_only` instead of `outside_corpus_range`. The same
regeneration restored the `বনলতা সেন` appendix tail page 172, making `তোমাকে`
`supported` and raising `ভিখিরী` from weak evidence to high token coverage.
Those results are evidence for the next OCR sidecar rebuild; the ignored JSONL
sidecars were not committed.

A later footer-tail repair recovered the `মহাপৃথিবী` সংযোজন tail in the shared
scan: visible page 173 is followed by footer-backed pages 174 and 175, then a
short inferred final page 176. Against that scratch corpus the citation audit
reduced `outside_corpus_range` rows from 5 to 2. In the same review pass,
`গভীর এরিয়েলে` was corrected from a stale `সাতটি তারার তিমির` p.163-164
citation to `বেলা অবেলা কালবেলা` printed pages 41-42, supported by the
`বেলা অবেলা কালবেলা` contents entry and page-local title/body OCR; the current
default citation audit at that point had 1 `outside_corpus_range` row. A later
cleanup removed the remaining stale unsupported citation rather than displaying
it as book-backed evidence, so the current default audit has no
`outside_corpus_range` rows.

The poem-body quality report also records leading dash structure across all
Jibanananda poem JSON files. A single leading ASCII hyphen is treated by the site
renderer as an imported stanza-break marker. Long all-hyphen divider lines are
hidden. Multi-hyphen starts such as `---ব’ লে...` and leading Bengali em dashes
are preserved as text and surfaced in the report for review, because those are
more likely to be punctuation than stanza separators.

## Test plan

- `uv run python -m py_compile scripts/ocr_page_corpus.py scripts/classify_pages.py scripts/repair_page_sequence.py scripts/propose_poem_spans.py scripts/extract_page_layout.py scripts/apply_poem_metadata.py scripts/ocr_lexicon_audit.py scripts/phrase_window_audit.py scripts/toc_index_audit.py scripts/fuzzy_line_audit.py scripts/ordered_region_audit.py scripts/citation_consistency_audit.py scripts/citation_repair_audit.py scripts/composition_date_audit.py scripts/report_metadata_gaps.py scripts/citation_factor_model.py scripts/source_url_marker_audit.py`
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
- Full metadata sprint for collection mapping, composition dates, duplicate titles, conflicting source markers, and proofing status.
