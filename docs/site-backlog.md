# Site backlog

## Desktop poet page timeline scroll

Source request: `http://127.0.0.1:4322/kobiyal/poets/jibanananda-das/`

On desktop, the left timeline/phase column should support independent scroll when the pointer is over that column and the user scrolls there. The main/right column can either keep following the active left-column section or remain independent, but clicking a left-column section must still scroll the corresponding right-column section into view.

Notes:

- Scope this to desktop layout only.
- Preserve current section-click behavior.
- Check sticky positioning and scroll-spy behavior after the change.

## Current metadata/OCR state

- Current request is to commit and push the current state; GitHub Pages deploy is
  expected from the main-branch push.
- 102 public `Jibanananda` poems still lack printed-page citations.
- 73 public `Jibanananda` poems still lack `source_year`.
- 102 public `Jibanananda` poems still lack primary printed-book year.
- 369 public `Jibanananda` poems still lack composition date.
- 66 poems still have unknown collection (`সংকলন অজানা`).
- Source corpus backlog: **105 records across 12 groups**.
- Full machine-readable acquisition queue:
  `metadata_reports/source-acquisition-manifest.current.json` with a companion
  Markdown summary at `metadata_reports/source-acquisition-manifest.current.md`.
  The manifest validation and rendered printed-page attribution checks are now in CI.
- Source URL marker audit status is now carried in the acquisition manifest:
  all 66 public unknown-source rows were checked with no explicit collection
  marker, and 36 known-source rows need corpus/page evidence rather than
  URL-marker classification.
- Priority blockers:
  - `aloprithibi`: 21 uncited known-source poems.
  - `srestha-kabita`: 3 existing citations need corpus/default audit coverage.
  - Archival buckets: `agranthita` 5, `aprakashita` 1.
  - 9 scanned-source rows are reviewed holds until a better scan, corpus, or direct printed-book review is available.
- Next work is source-corpus acquisition and direct printed-book review before any
  further citation automation.
- Current verification status: `report_metadata_gaps` passed, `citation_factor_model` passed, `bun run build` passed.
