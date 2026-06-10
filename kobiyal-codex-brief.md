# kobiyal — Codex build brief

Working name: **কবিয়াল** (repo: `kobiyal`). Named for the Bengali folk-poet/bard tradition.

## Goal
Build a beautiful, mobile-first **static** site that gives Bengali poetry the
"Rekhta experience": effortless reading, exploration by poet and by the *phases*
of a poet's creative life, and tasteful share cards for Instagram. Deploy to
GitHub Pages. No backend, no accounts, no payment — reading is free; this repo is
the **free public-domain archive only**.

## Design references (study, do not copy)
- poetry.org / poetryfoundation.org → restraint, typographic calm, whitespace.
- rekhta.org → depth of navigation (poet → body of work), shareable poem cards,
  the feeling that the *text itself* is the hero.
The aesthetic target is "an exquisite object you want to share," not a database UI.

## HARD CONSTRAINTS (do not violate)
1. **PUBLIC DOMAIN ONLY.** Ingest poems ONLY for this allowlist (India, life + 60):
   Rabindranath Tagore (d. 1941), Jibanananda Das (d. 1954),
   Sukanta Bhattacharya (d. 1947), Michael Madhusudan Dutt, Satyendranath Dutta,
   Dwijendralal Ray, Kamini Roy, and pre-modern / Vaishnava poets (Chandidas,
   Ramprasad Sen). **Explicitly EXCLUDE** Kazi Nazrul Islam and Jasimuddin
   (both d. 1976 → in copyright until 2037).
2. **LANGUAGE POLICY.** Every human-facing string — UI labels, navigation,
   buttons, poem text, bios, phase labels, attributions, share-card text — is in
   **Bengali**. Only *code* stays English: identifiers, JSON keys, slugs, comments,
   and this brief. No English chrome anywhere a reader can see it. English `*_en`
   fields below are **dev-only slugs/aids and must never render**.
3. **Static only.** Output deploys to GitHub Pages with no server. No DB, no API keys.
4. **Mobile-first.** Design for a 390px phone first; desktop is a calm, minimal
   widescreen version of the *same* layout — never a separate one.
5. **Attribution mandatory** on every poem: poet, title, source edition, year if
   known. Carry a `verified: false` flag — proofreading is a LATER pass; do not
   block on it, but keep the flag visible in the data.
6. **Periodisation is an EDITORIAL LENS, not fact.** Every phase scheme carries a
   `periodisation_source` and renders *as a reading* (e.g. a small "এই বিভাজন
   একটি পাঠ, ধ্রুব সত্য নয়" note). Never present life-phases as objective truth.
7. **Never fabricate** text, dates, translations, or word-meanings. If unknown, omit.

## Tech stack
- **Astro + TypeScript**, static output (`output: 'static'`), zero-runtime by
  default, islands only where interaction is needed.
- **Data layer:** typed JSON in `/src/data`, loaded via content collections. The
  site is an ENGINE over this data — no hand-built per-poem pages.
- **Share cards:** client-side via `html-to-image`, capturing a hidden 1080×1350
  template node. Ensure web fonts are fully loaded before capture.
- **Fonts:** Noto Serif Bengali (poem body/display), Hind Siliguri (UI). Self-host
  via fontsource. Verify conjunct (যুক্তাক্ষর) rendering. Verse line-height ~1.9.

## Data model (TS types; store as JSON)
```ts
Poet  { id, name_bn, name_en /*dev slug only*/, born, died, pd_status,
        bio_bn, phases: Phase[], strands?: Phase[] }
Phase { id, label_bn, label_en /*dev only*/, year_range, collections_bn: string[],
        palette: { bg, ink, accent }, motif_bn, note_bn /*short, reader-facing*/,
        chronological: boolean, periodisation_source /*English, dev/editorial*/,
        rationale /*English, dev note: imagery → palette justification*/ }
Poem  { id, poet_id, phase_id, title_bn, body_bn, tags: string[],
        source_edition, source_year, verified: boolean }
Tag   { id, label_bn, label_en /*dev only*/ }
// Tags drive ALL navigation. No recommender yet, but keep the schema clean so
// engagement weighting can be added later as an offline weekly refresh.
```

## Seed data task
1. Clone `github.com/shuhanmirza/Bengali-Poem-Dataset` into `/scratch` (read-only).
   NOTE: it holds 137 poets, **many in copyright**, and its GPL-3.0 covers the
   *compilation*, not the underlying texts.
2. Write a one-off ingest script that parses **only allowlisted poets** into the
   Poem schema. Discard everything else. `verified=false`; fill `source` where the
   dataset provides it, else `"unknown"`. Do NOT copy GPL code into `/src`.
3. Fully realize **Jibanananda Das** end-to-end using the phase definitions in the
   "Reference data" section below. Then add Tagore + one more poet for breadth once
   the engine works.

## Pages & features
- **Home:** a quiet gallery of poet cards. One tap → poet page.
- **Poet page:** short Bengali bio + a PHASE TIMELINE. Each phase is a segment that
  **illuminates in its own palette** as the reader navigates/scrolls into it.
  Tapping a phase filters that phase's poems. Parallel *strands* render distinctly
  from the chronological phases (see রূপসী বাংলা below).
- **Poem page:** the text is the hero — large, well-set verse on the phase's
  palette. Bengali attribution line beneath. One unobtrusive share affordance.
- **Tag navigation:** browse by tag across all poets.
- **Share-card generator:** reader selects lines (or a short whole poem) → 1080×1350
  Instagram poster: lines in Noto Serif Bengali on the phase palette/motif, small
  tasteful Bengali wordmark + handle. Download + native share.

## Interaction principle
The site must **NEVER nag** to share. Reward exploration (phases lighting up) and
make the generated card so beautiful that sharing feels natural. Tie any share
moment to a genuine *arrival* (finishing a phase / reaching a poem) — never a timed
popup.

## Milestones (commit at each; small PRs)
- **M1:** Astro scaffold + data types + ingest script + Jibanananda data in JSON.
- **M2:** Design tokens + fonts + poet card + poem page (static, no interaction).
- **M3:** Phase timeline with the light-up-on-navigate interaction + strand rendering.
- **M4:** Tag navigation across poets.
- **M5:** Share-card generator (1080×1350, fonts-loaded capture, branding).
- **M6:** Add Tagore + 1 more poet; polish mobile; GitHub Pages deploy workflow.

## Acceptance criteria
- Lighthouse mobile ≥ 95 perf / 100 a11y.
- Bengali conjuncts render correctly on iOS Safari + Android Chrome.
- Every poem shows full Bengali attribution; periodisation renders as a reading.
- Zero in-copyright poets anywhere in `/src/data`.
- No English text visible to readers.
- Builds to `/dist` as pure static; GitHub Pages deploy is green.

## DO NOT
- Add analytics, accounts, payments, or any backend.
- Ingest poets outside the allowlist, or copy GPL code from the seed repo into `/src`.
- Invent translations, word-glosses, biographical dates, or phase boundaries.
- Present life-phases as fact, or build per-poem hardcoded pages.
- Render any English string in the reader-facing UI.

---

# Reference data: Jibanananda Das — phase definitions

This is the **worked example** the engine and every other poet should match.
Method: each palette is **derived from the dominant imagery of that period's
collections**, never chosen decoratively. The chronological spine (collection
years) is factual; the tonal reading is editorial and attributed.

`periodisation_source` (applies to all phases below):
> Editorial lens structured on Jibanananda's published collections (composition
> dates noted where they differ from publication). Interpretive framing informed by
> Clinton B. Seely, *A Poet Apart* (1990), and the chronology in the standard
> Bhumendra Guha / Abdul Mannan Syed editions. Phase boundaries are a reading,
> not a fact.

```json
{
  "id": "jibanananda-das",
  "name_bn": "জীবনানন্দ দাশ",
  "name_en": "jibanananda-das",
  "born": 1899,
  "died": 1954,
  "pd_status": "public-domain-india-since-2015",
  "bio_bn": "বাংলা কবিতার অন্যতম প্রধান আধুনিক কণ্ঠস্বর; বুদ্ধদেব বসুর ভাষায় 'নির্জনতম কবি'। ধূসরতা, সন্ধ্যা আর বাংলার রূপ তাঁর কবিতার কেন্দ্রে।",
  "phases": [
    {
      "id": "jhara-palak",
      "label_bn": "ঝরা পালক — ধার-করা স্বর",
      "label_en": "jhara-palak",
      "year_range": "১৯২৭",
      "collections_bn": ["ঝরা পালক (১৯২৭)"],
      "palette": { "bg": "#F2E7DE", "ink": "#4A2A26", "accent": "#BC7A5E" },
      "motif_bn": "ঝরে-পড়া একটি পালক; অস্তরাগ",
      "note_bn": "অস্তরাগের রং — তখনও কণ্ঠস্বর ধার-করা, অলংকারে ভারী।",
      "chronological": true,
      "periodisation_source": "see shared note above",
      "rationale": "Faded rose-cream ground (#F2E7DE) with maroon-brown ink and an old-gold/terracotta accent: the sunset-glow, feathers, and decorative sensuousness of an apprentice voice still echoing Keats, Mohitlal and early Nazrul. Warm and ornamental — deliberately the LEAST distinctive palette, because the work is not yet his own."
    },
    {
      "id": "dhusar-pandulipi",
      "label_bn": "ধূসর পাণ্ডুলিপি — নিজস্ব কণ্ঠস্বর",
      "label_en": "dhusar-pandulipi",
      "year_range": "১৯৩৬",
      "collections_bn": ["ধূসর পাণ্ডুলিপি (১৯৩৬)"],
      "palette": { "bg": "#D7D4CB", "ink": "#2C2C29", "accent": "#8A7A55" },
      "motif_bn": "ধুলোমাখা ধূসর পাণ্ডুলিপির পাতা",
      "note_bn": "ছাই আর ধুলোর ধূসর — এইখানে কবি পেলেন নিজের কণ্ঠ।",
      "chronological": true,
      "periodisation_source": "see shared note above",
      "rationale": "Ash-grey paper ground (#D7D4CB), charcoal ink, faded-ochre accent. The collection literally names its colour — ধূসর (grey/ashen) — making this the clearest proof of colour-from-content: dust, time, sediment, weariness, the awakening of his true voice. The cleanest case in the whole scheme."
    },
    {
      "id": "banalata-sen",
      "label_bn": "বনলতা সেন — সন্ধ্যা ও অনন্ত",
      "label_en": "banalata-sen",
      "year_range": "১৯৪২",
      "collections_bn": ["বনলতা সেন (১৯৪২)"],
      "palette": { "bg": "#283642", "ink": "#E7E1D5", "accent": "#C98A45" },
      "motif_bn": "সন্ধ্যায় ঘরে-ফেরা পাখি; অন্ধকার",
      "note_bn": "সন্ধ্যা আর অন্ধকার — ঘরে-ফেরা পাখির ডানায় হাজার বছরের ক্লান্তি।",
      "chronological": true,
      "periodisation_source": "see shared note above",
      "rationale": "Deep twilight indigo-slate ground (#283642), dusk-cream ink, a single last-amber accent (#C98A45). The first phase to invert to a dark surface — the homing bird at evening, andhakar, the face in darkness, the thousand-year weariness of বনলতা সেন. The amber is the day's last light, not decoration."
    },
    {
      "id": "mahaprithibi-timir",
      "label_bn": "মহাপৃথিবী ও সাতটি তারার তিমির — অন্ধকারের বিস্তার",
      "label_en": "mahaprithibi-timir",
      "year_range": "১৯৪৪–১৯৪৮",
      "collections_bn": ["মহাপৃথিবী (১৯৪৪)", "সাতটি তারার তিমির (১৯৪৮)"],
      "palette": { "bg": "#14181F", "ink": "#CDD3DA", "accent": "#6E8694" },
      "motif_bn": "তারার ফাঁকে তিমির; বিস্তীর্ণ রাত",
      "note_bn": "তারার তিমির — যুদ্ধ, মৃত্যু আর মহাপৃথিবীর কালো বিস্তার।",
      "chronological": true,
      "periodisation_source": "see shared note above",
      "rationale": "Near-black blue ground (#14181F), cold-starlight ink, steel-silver accent. The darkest palette for the bleakest phase: war, famine years, death, the great earth, and তিমির — the darkness of/between the seven stars. Cold and vast where বনলতা সেন was warm and intimate."
    }
  ],
  "strands": [
    {
      "id": "rupasi-bangla",
      "label_bn": "রূপসী বাংলা — সমান্তরাল সবুজ",
      "label_en": "rupasi-bangla",
      "year_range": "রচনা ~১৯৩২–৩৪ · প্রকাশ ১৯৫৭ (মরণোত্তর)",
      "collections_bn": ["রূপসী বাংলা (রচনা ~১৯৩২–৩৪; প্রকাশ ১৯৫৭)"],
      "palette": { "bg": "#DCE5D2", "ink": "#243018", "accent": "#2E7D8A" },
      "motif_bn": "ধানসিঁড়ি নদী, মাছরাঙা, সবুজ বাংলা",
      "note_bn": "ধূসর পর্বের পাশেই লেখা — অথচ একেবারে উল্টো সবুজ সুর। তাই এটি ধারাবাহিক পর্ব নয়, একটি সমান্তরাল স্রোত।",
      "chronological": false,
      "periodisation_source": "see shared note above — flagged as a NON-chronological parallel strand",
      "rationale": "Verdant paddy-green ground (#DCE5D2), deep-forest ink, kingfisher-teal accent. CRITICAL HONESTY CASE: composed alongside the grey ধূসর পাণ্ডুলিপি period yet tonally its opposite — lush pastoral Bengal (ধানসিঁড়ি, মাছরাঙা, rivers). It MUST render as a parallel strand, not slotted into the linear timeline, so the design does not flatten a real simultaneity into a false progression. This is the pattern to copy for every poet: when a body of work contradicts the neat arc, surface it, don't bury it."
    }
  ]
}
```
