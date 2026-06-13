import type { CollectionEntry } from "astro:content";

export type Poet = CollectionEntry<"poets">["data"];
export type Poem = CollectionEntry<"poems">["data"];
export type Tag = CollectionEntry<"tags">["data"];
export type Phase = Poet["phases"][number];

const duplicatePoemIds = new Set([
  "jibanananda-225",
  "jibanananda-190",
  "jibanananda-214",
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

export function firstLines(body: string, count = 3): string[] {
  return splitStanzas(body).flat().slice(0, count);
}
