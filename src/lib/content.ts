import type { CollectionEntry } from "astro:content";

export type Poet = CollectionEntry<"poets">["data"];
export type Poem = CollectionEntry<"poems">["data"];
export type Tag = CollectionEntry<"tags">["data"];
export type Phase = Poet["phases"][number];
export type VerseBlock =
  | { kind: "stanza"; lines: string[] }
  | { kind: "section-marker"; marker: string }
  | { kind: "source-note"; text: string };

const duplicatePoemIds = new Set([
  "jibanananda-225",
  "jibanananda-190",
  "jibanananda-214",
  "jibanananda-029",
  "jibanananda-094",
  "jibanananda-099",
  "jibanananda-131",
  "jibanananda-227",
  "jibanananda-276",
  "jibanananda-281",
  "jibanananda-301",
  "jibanananda-348",
  "jibanananda-349",
  "jibanananda-350",
  "jibanananda-361",
  "jibanananda-374",
]);

export function allPhases(poet: Poet): Phase[] {
  return [...poet.phases, ...(poet.strands ?? [])];
}

export function findPhase(poet: Poet, phaseId: string): Phase {
  const phase = allPhases(poet).find((item) => item.id === phaseId);
  if (!phase) {
    throw new Error(`Missing phase ${phaseId} for ${poet.id}`);
  }
  return phase;
}

function comparePoems(a: Poem, b: Poem): number {
  return (a.sort_order ?? Number.MAX_SAFE_INTEGER) - (b.sort_order ?? Number.MAX_SAFE_INTEGER)
    || a.title_bn.localeCompare(b.title_bn, "bn");
}

export function poemsForPoet(poems: Poem[], poetId: string): Poem[] {
  return poems.filter((poem) => poem.poet_id === poetId).sort(comparePoems);
}

export function publicPoems(poems: Poem[]): Poem[] {
  return poems.filter((poem) => !duplicatePoemIds.has(poem.id));
}

export function poemCountForPhase(poems: Poem[], poetId: string, phaseId: string): number {
  return poems.filter((poem) => poem.poet_id === poetId && poem.phase_id === phaseId).length;
}

export function splitStanzas(body: string): string[][] {
  return body
    .trim()
    .split(/\n{2,}/)
    .map((stanza) => stanza.split("\n").map((line) => line.trim()).filter(Boolean))
    .filter((stanza) => stanza.length > 0);
}

const banglaDigitPattern = "[০-৯]";
const standaloneSectionMarkerPattern = new RegExp(`^${banglaDigitPattern}+$`);
const wrappedSectionMarkerPattern = new RegExp(`^(.*?)\\s*।।\\s*(${banglaDigitPattern}{1,2})\\s*।।\\s*$`);
const trailingBareMarkerPattern = new RegExp(`^(.*?)([।!?;:,]?)\\s*(?<!${banglaDigitPattern})(${banglaDigitPattern})\\s*([!।?])?\\s*$`);
const sourceNotePattern = /^(?:(দেশ|কবিতা),?\s+.*[০-৯]{3,4}|গ্রন্থ\s*:\s*\S.*)$/;
const dashDividerPattern = /^-{5,}$/;
const leadingStanzaBreakPattern = /^-(?!-)\s*(.*)$/;

function stripTrailingBareMarker(line: string): string {
  const match = line.match(trailingBareMarkerPattern);
  if (!match) return line;
  const [, before, precedingPunctuation, , followingPunctuation] = match;
  return `${before}${precedingPunctuation}${followingPunctuation ?? ""}`.trim();
}

function pushStanza(blocks: VerseBlock[], lines: string[]) {
  if (lines.length > 0) {
    blocks.push({ kind: "stanza", lines: [...lines] });
    lines.length = 0;
  }
}

function normalizeLeadingStanzaBreak(line: string): { startsNewStanza: boolean; line: string } {
  const match = line.trimStart().match(leadingStanzaBreakPattern);
  if (!match) return { startsNewStanza: false, line };
  return { startsNewStanza: true, line: match[1].trim() };
}

export function verseBlocks(body: string): VerseBlock[] {
  const blocks: VerseBlock[] = [];

  for (const stanza of splitStanzas(body)) {
    const currentLines: string[] = [];

    for (const rawLine of stanza) {
      if (dashDividerPattern.test(rawLine.trim())) {
        pushStanza(blocks, currentLines);
        continue;
      }

      const { startsNewStanza, line } = normalizeLeadingStanzaBreak(rawLine);
      if (startsNewStanza) {
        pushStanza(blocks, currentLines);
        if (!line) continue;
      }

      if (standaloneSectionMarkerPattern.test(line)) {
        pushStanza(blocks, currentLines);
        blocks.push({ kind: "section-marker", marker: line });
        continue;
      }

      if (sourceNotePattern.test(line)) {
        pushStanza(blocks, currentLines);
        blocks.push({ kind: "source-note", text: line });
        continue;
      }

      const wrappedMarker = line.match(wrappedSectionMarkerPattern);
      if (wrappedMarker) {
        const cleanedLine = wrappedMarker[1].trim();
        if (cleanedLine) currentLines.push(cleanedLine);
        pushStanza(blocks, currentLines);
        blocks.push({ kind: "section-marker", marker: wrappedMarker[2] });
        continue;
      }

      const cleanedLine = stripTrailingBareMarker(line);
      if (cleanedLine) currentLines.push(cleanedLine);
    }

    pushStanza(blocks, currentLines);
  }

  return blocks;
}

export function firstLines(body: string, count = 3): string[] {
  return verseBlocks(body)
    .filter((block): block is Extract<VerseBlock, { kind: "stanza" }> => block.kind === "stanza")
    .flatMap((block) => block.lines)
    .slice(0, count);
}
