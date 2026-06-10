# kobiyal — Claude Design brief: build the design system

You are designing the visual system for **কবিয়াল** (`kobiyal`), a mobile-first,
static, public-domain Bengali poetry site. This is the companion to the engineering
build brief and to `kobiyal-strings-bn.json` (the Bengali UI strings). Design only —
no code required — but every decision must be implementable as a static site driven
by CSS custom properties.

## What kobiyal is
A free, non-commercial reading space that gives Bengali poetry the "Rekhta
experience": effortless reading, exploration by poet and by the *phases* of a poet's
creative life, and tasteful share cards for Instagram. The text is the reason anyone
is here.

## Five principles the design must express
1. **The text is the hero.** Verse leads; chrome recedes. Generous space, quiet UI.
2. **Colour comes from the work, not onto it.** Each poet-phase has a palette
   *derived from that period's imagery* (see data below). This is the signature
   mechanic — design the system around it.
3. **Tasteful, never naggy.** Sharing is a reward at a genuine arrival (finishing a
   phase, reaching a poem), never a timed popup or a nag.
4. **Mobile-first; calm on desktop.** Design the 390px phone first; desktop is a
   serene widescreen version of the *same* layout, not a different one.
5. **Honest.** Periodisation renders as a *reading*, not fact. Attribution is always
   present. A parallel strand is never flattened into a false progression.

## Hard constraints
- **Bengali only** for anything a reader sees. Use REAL Bengali set in
  **Noto Serif Bengali** (verse/display) and **Hind Siliguri** (UI). Pull labels
  from `kobiyal-strings-bn.json`. Never use lorem ipsum or invented Bengali.
- **Never invent or approximate poem text.** Use real Jibanananda verse from the
  kobiyal dataset for mockups. If exact verse is not available in-canvas, use a
  clearly-marked neutral placeholder block sized and shaped like real verse — never
  fabricated Bengali lines. (This mirrors the project's own no-fabrication rule.)
- Implementable with **CSS custom properties**; the phase palette must be a
  **swappable token set**, not bespoke styling per page.
- **Mobile frame 390px**; also show desktop at ~1280px for key screens.
- **WCAG AA contrast** must hold for every phase palette (verify ink-on-bg).
- Render all numerals in **Bengali numerals** (০–৯).

## Colour architecture (the crux — design this first)
Two layers that coexist:

- **Shell** — a constant, near-neutral chrome used site-wide (header, nav, footer,
  default page background, base type colour). This gives kobiyal one stable identity
  no matter whose poems you're reading.
- **Phase skin** — a per poet-phase `{ bg, ink, accent }` that themes ONLY the
  reading surfaces: the poet page body, the poem reading view, the phase timeline's
  active segment, and the share card. The shell frames it; the skin floods the page.

Define semantic tokens that map 1:1 to CSS variables, e.g.
`--shell-bg, --shell-ink, --shell-hairline, --phase-bg, --phase-ink, --phase-accent`
plus any derived muted/translucent steps you need. Design against **all** the
Jibanananda skins below, and explicitly prove the mechanic on the two hardest-
contrasting skins — **ধূসর পাণ্ডুলিপি** (light ash) and **মহাপৃথিবী/তিমির** (near-black).
If a component holds in both, it holds everywhere.

## Shell & visual language (the gallery direction — final)
The shell is a **near-white gallery**: the poetry.org feeling — modern minimalism,
quiet restraint — applied to Bengali poetry. It exists to make the phase skins, and
the verse, the only things that speak.

- **Shell colour:** near-white, NOT pure white. Base ~`#FAFAF8` with a hairline
  ~`#E8E8E4` and a soft graphite ink ~`#2B2B28`. Pure `#FFFFFF` reads clinical and
  fights the lit phase skins; a faint warmth keeps it gallery, not hospital.
- **Type carries the shell/skin division:** **serif = the work** (Noto Serif Bengali
  for verse, poet names, phase labels — everything *inside* the phase skin);
  **sans = the gallery** (Hind Siliguri for nav, captions, attribution, chips — the
  neutral shell). Type then reinforces the two-layer architecture instead of cutting
  across it.
- **Design assets — organic, sans-serif manner:** restrained, line-based motifs
  (a feather, a river-line, a star-scatter, a homing bird) drawn flat, geometric and
  single-weight — the visual logic of a sans-serif, NOT ornate illustration. Sparse,
  never decorative clutter.
- **Assets ride the skin system too:** each motif is tinted by its phase `accent`, so
  the organic marks move with the palette rather than floating independently of it.
  In the neutral shell, any standing motif uses the graphite ink at low opacity.

Net feel: a calm white gallery whose rooms (each poet-phase) are lit in their own
colour, with serif verse as the artwork and quiet sans-serif labels on the walls.

## Typography system to define
- **Verse (poem body):** Noto Serif Bengali; line-height ~1.9; comfortable reading
  measure; clear stanza spacing. This is the largest, most cared-for type on the site.
- **Display (poet names, phase labels):** Noto Serif Bengali, larger weights.
- **UI (nav, labels, attribution, chips):** Hind Siliguri, smaller and quieter.
- **Disclaimer / attribution:** small, graceful, never shouty.
- Specify mobile and desktop scales, and verify conjunct (যুক্তাক্ষর) rendering.

## Components to design (resting + key states)
1. **Poet card** — home gallery tile.
2. **Phase timeline** — THE signature element. Design (a) the resting state (phases
   quiet/dim) and (b) the illuminated state where the phase you navigate to lights up
   in its own skin. Show how a **parallel strand** (রূপসী বাংলা) is visually distinct
   from the chronological phases — offset, different marker/shape — so the timeline
   never reads as a single false progression.
3. **Poem reading view** — verse hero on the phase skin, Bengali attribution beneath.
4. **Attribution block** — কবি · কাব্যগ্রন্থ · প্রকাশ · সূত্র.
5. **Theme/tag chip** + theme page header.
6. **Share affordance + 1080×1350 Instagram share card.** Card = selected lines in
   Noto Serif Bengali on the phase skin/motif, a small **কবিয়াল** wordmark + handle,
   and a poet · collection credit line. Render it in at least the two contrasting skins.
7. **Header / nav / footer** (Bengali, from the strings file).
8. **Periodisation disclaimer** treatment — small, calm: "এই পর্ব-বিভাজন একটি পাঠ — কোনো ধ্রুব সত্য নয়।"
9. **"রঙের কথা"** micro-note — the optional one-line "why this hue" treatment.

## Motion / interaction (light touch)
- Phase illumination on navigate / scroll-into: gentle, ~250–350ms, eased.
- Share moment surfaces on arrival only — never timed, never a popup.
- No nag patterns anywhere.

## Deliverables
1. A one-screen **design-principles board**.
2. A **token spec**: full colour (shell + the phase-skin system with every
   Jibanananda skin), type scale, spacing rhythm — structured to map directly to CSS
   custom properties.
3. **High-fidelity mockups** on a 390px mobile frame: home, poet page (with the phase
   timeline), poem reading view, theme page. Plus desktop (~1280px) of poet page and
   poem page.
4. The **phase timeline** in resting + illuminated states, including the parallel-
   strand treatment for রূপসী বাংলা.
5. The **1080×1350 share card** in both the ধূসর পাণ্ডুলিপি and মহাপৃথিবী/তিমির skins.
6. A short rationale tying each major choice back to the five principles.

## Embedded data

**Fonts:** Noto Serif Bengali (verse/display), Hind Siliguri (UI).

**Strings:** use `kobiyal-strings-bn.json` verbatim for all labels.

**Jibanananda phase skins** (derive nothing decoratively — these palettes already
come from the imagery; honour them):

```json
[
  { "id": "jhara-palak",        "label_bn": "ঝরা পালক — ধার-করা স্বর",
    "motif_bn": "ঝরে-পড়া একটি পালক; অস্তরাগ",
    "palette": { "bg": "#F2E7DE", "ink": "#4A2A26", "accent": "#BC7A5E" }, "chronological": true },
  { "id": "dhusar-pandulipi",   "label_bn": "ধূসর পাণ্ডুলিপি — নিজস্ব কণ্ঠস্বর",
    "motif_bn": "ধুলোমাখা ধূসর পাণ্ডুলিপির পাতা",
    "palette": { "bg": "#D7D4CB", "ink": "#2C2C29", "accent": "#8A7A55" }, "chronological": true },
  { "id": "banalata-sen",       "label_bn": "বনলতা সেন — সন্ধ্যা ও অনন্ত",
    "motif_bn": "সন্ধ্যায় ঘরে-ফেরা পাখি; অন্ধকার",
    "palette": { "bg": "#283642", "ink": "#E7E1D5", "accent": "#C98A45" }, "chronological": true },
  { "id": "mahaprithibi-timir", "label_bn": "মহাপৃথিবী ও সাতটি তারার তিমির — অন্ধকারের বিস্তার",
    "motif_bn": "তারার ফাঁকে তিমির; বিস্তীর্ণ রাত",
    "palette": { "bg": "#14181F", "ink": "#CDD3DA", "accent": "#6E8694" }, "chronological": true },
  { "id": "rupasi-bangla",      "label_bn": "রূপসী বাংলা — সমান্তরাল সবুজ",
    "motif_bn": "ধানসিঁড়ি নদী, মাছরাঙা, সবুজ বাংলা",
    "palette": { "bg": "#DCE5D2", "ink": "#243018", "accent": "#2E7D8A" }, "chronological": false }
]
```

(Full poet/phase data, including rationale and attribution, lives in
`kobiyal-codex-brief.md`. রূপসী বাংলা is a **non-chronological parallel strand** —
treat it visually as such.)

## Where to start
Begin with the **token spec** and the **two contrasting poem reading views**
(ধূসর পাণ্ডুলিপি and মহাপৃথিবী/তিমির). Prove the shell + phase-skin mechanic there
before fanning out to the rest of the screens.
