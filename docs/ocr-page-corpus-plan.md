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
  It also allows a short trusted tail after two visible sequential anchors; this
  handles cases where the final poem page in a section has no readable printed
  page number but directly follows visible pages.

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
  source. The default apply scope skips duplicate-hidden poem imports listed in
  `src/lib/content.ts`; use `--include-duplicates` only for a deliberate
  duplicate-cleanup pass.

- `scripts/report_metadata_gaps.py`
  Builds a review-only Markdown/JSON inventory of public Jibanananda records
  that still lack printed book page citations. It joins the current poem JSON
  with a span-candidate report, excludes hidden duplicate imports, and ranks
  gaps into buckets such as `manual_collection_review`,
  `needs_printed_page_sequence`, `weak_text_anchor`,
  `token_or_title_only_candidate`, and `no_candidate`. This is
  the handoff list for printed-source review; it does not apply metadata. It
  also reads `src/data/metadata-review-exclusions.json`, a committed audit trail
  of candidate-specific false positives. Reviewed exclusions remain in the
  missing-citation count, but are moved to `reviewed_*` buckets so the next
  review pass focuses on unresolved evidence instead of rechecking disproved
  title collisions or weak one-line overlaps. With `--page-corpus`, it also
  checks each known-source gap against the matching scanned collection and
  reports whether the source book is unscanned, weakly supported by OCR tokens,
  or supported by title/line evidence.

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
  contiguous OCR windows only. Each embedding has two feature channels: exact
  contiguous character shingles at full weight, and OCR class-normalized
  contiguous character shingles at half weight. Longer contiguous shingles
  (`3,5,8` by default) get higher weights. Region embeddings are built lazily
  from per-page inverted indexes, so expensive vector dots do not run over every
  OCR window in the corpus. When NumPy is available, the same page-local sparse
  postings are scored with vectorized dot accumulation; this is an acceleration
  of the contiguous-region matcher, not a page-wide embedding shortcut. The
  detailed pass still requires each accepted poem line to match one local OCR
  region, then verifies that local region with the longest contiguous exact and
  OCR-class character runs. Exact contiguous runs count fully; class-only
  contiguous runs add half credit. It reports both the longest consecutive run
  of matched poem lines and the ordered run of local OCR regions. Candidate
  windows are capped to short printed-page spans. This report is not an apply
  source; fuzzy-only candidates must still be checked against printed-page
  evidence before writing poem metadata.

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
  mutate poem JSON. The current run checks 270 existing citations: 238 are
  supported by title/opening/exact-line evidence, 15 are supported only by broad
  token coverage, 14 cite pages outside the current OCR corpus range for that
  book, and 3 are weak current citations that need manual source review.

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

- 279 of 389 Jibanananda records have printed book citations.
- 270 of 373 public/non-duplicate Jibanananda records have printed book citations.
- 103 public/non-duplicate Jibanananda records still lack printed book citations.
- 85 public/non-duplicate records still have `সংকলন অজানা` as the collection.
- 89 public/non-duplicate records still lack `source_year`.
- Public counts exclude exact or partial duplicate import rows, including the
  parenthesized `সূর্য নক্ষত্র নারী` fragments and the alternate `সেই দিন এই
  মাঠ` import whose cited `রূপসী বাংলা` counterpart remains listed, plus the
  stanza-only `চারিদিকে শান্ত বাতি` fragment and the composite dialogue import
  `আকাশে সাতটি তাঁরা`.

Remaining `সংকলন অজানা` poems stay in the metadata backlog until a manual or stronger automated pass resolves them.

The enhanced gap report now separates known-source failures. In the current
sidecar run, 11 known-source gaps cite editions not mapped to a current OCR book
corpus (`আলোপৃথিবী`, `শ্রেষ্ঠ কবিতা`, `অগ্রন্থিত কবিতা`, or `অপ্রকাশিত
কবিতা`). Seven known-source gaps do target scanned collections, but six have
only weak same-source OCR support and one has token-only support; none has title
or exact-line evidence strong enough for automatic page citation.

A manual evidence pass over the strongest remaining line-anchor candidates added
`ভিখিরী` to the `বনলতা সেন` appendix on printed pages 171-172 and `হাঁস` to
`সাতটি তারার তিমির` printed page 21. The same pass identified
`স্বাতীতারা` as a duplicate import of the already-cited `পটভূমির` record and
hid it from public routes.

A follow-up schema normalization added the missing `primary` role to three
older printed-page citations (`অঘ্রাণ`, `অনন্ত জীবন যদি পাই আমি`, and
`অনির্বাণ`) so review reports and overwrite guards count them as existing
book-backed sources.

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
are `token_or_title_only_candidate` matches or no-candidate records.

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
current full run in about 85 seconds including page-window setup. It found zero
matched poems. Treat this as negative evidence for automatic fuzzy application:
the remaining gaps need better OCR/source extraction or manual printed-source
review rather than looser fuzzy citation writes.

The fuzzy verifier now treats page-wide overlap as recall-only and local
contiguity as the evidence gate. Its vector features are still hashable and
cacheable, but every scored feature comes from a contiguous character shingle
inside a bounded OCR line window, with a parallel OCR-equivalence-class channel.
The follow-up line score measures the longest contiguous substring inside that
same window, so repeated OCR confusions can improve a weak partial match without
letting unrelated tokens from different page regions combine into a citation.

The ordered-region audit confirms the same result from a stricter angle. Its
default gate found zero candidates. A permissive diagnostic run found three weak
rows (`কত দিন ঘাসে আর মাঠে`, `গল্পে আমি পড়িয়াছি কাঞ্চী কাশী বিদিশার কথা`,
and `সে`), but each had only one ordered run and matched common local phrases
rather than a stable poem span. No poem JSON should be updated from these rows.

The TOC index audit parsed 233 title/page entries from the repaired corpus,
including continuation TOC pages and logical sections in the shared
`banalata-sen` scan. The current normal-threshold run found one title hit,
`উদয়াস্ত`, but marked it `duplicate_title_conflict`: another local
`উদয়াস্ত` record already has stronger line-anchor evidence, so a TOC title
alone cannot identify which imported body should receive the citation. A
follow-up run also compared TOC entries with each poem's first substantial body
lines. At the normal threshold it still found no additional rows; at a lower
diagnostic threshold it produced only weak title matches and one missing-page
sequence row. No poem JSON should be updated from the current TOC report.

The apply script dry-run now uses the same duplicate-hidden scope as the public
site by default. With all review gates enabled against the current regenerated
span candidates, it reports `changed: []`; the only previously writable row was
already a hidden duplicate import. This is negative evidence for further
automatic public metadata writes from the current sidecars.

The citation consistency audit distinguishes absent scan coverage from
potentially wrong citations. In the current run, 14 rows are
`outside_corpus_range`: their cited printed pages sit outside the repaired OCR
page range for the named book, so the audit cannot prove or disprove them from
the current sidecars. Only 3 existing citations have in-range OCR rows but weak
title/line/token evidence. These should be manually checked against printed
sources before any metadata rewrite.

The poem-body quality report also records leading dash structure across all
Jibanananda poem JSON files. A single leading ASCII hyphen is treated by the site
renderer as an imported stanza-break marker. Long all-hyphen divider lines are
hidden. Multi-hyphen starts such as `---ব’ লে...` and leading Bengali em dashes
are preserved as text and surfaced in the report for review, because those are
more likely to be punctuation than stanza separators.

## Test plan

- `uv run python -m py_compile scripts/ocr_page_corpus.py scripts/classify_pages.py scripts/repair_page_sequence.py scripts/propose_poem_spans.py scripts/extract_page_layout.py scripts/apply_poem_metadata.py scripts/ocr_lexicon_audit.py scripts/phrase_window_audit.py scripts/toc_index_audit.py scripts/fuzzy_line_audit.py scripts/ordered_region_audit.py scripts/citation_consistency_audit.py scripts/report_metadata_gaps.py`
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
