import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const paletteSchema = z.object({
  bg: z.string(),
  ink: z.string(),
  accent: z.string(),
});

const phaseSchema = z.object({
  id: z.string(),
  label_bn: z.string(),
  label_en: z.string(),
  year_range: z.string(),
  collections_bn: z.array(z.string()),
  palette: paletteSchema,
  motif_bn: z.string(),
  motif_key: z.enum(["feather", "page", "bird", "stars", "river", "boat", "flame"]),
  note_bn: z.string(),
  bio_context_bn: z.string().optional(),
  chronological: z.boolean(),
  periodisation_source: z.string(),
  rationale: z.string(),
});

const purchaseLinkSchema = z.object({
  label_bn: z.string(),
  url: z.string().url(),
  note_bn: z.string().nullable().optional(),
});

const bookSourceSchema = z.object({
  role: z.string(),
  title_bn: z.string(),
  publisher_bn: z.string().nullable().optional(),
  edition_bn: z.string().nullable().optional(),
  publication_year: z.number().nullable().optional(),
  isbn: z.string().nullable().optional(),
  purchase_url: z.string().url().nullable().optional(),
  page_start: z.number().nullable().optional(),
  page_end: z.number().nullable().optional(),
  page_label_bn: z.string().nullable().optional(),
  page_basis: z.enum(["printed_page", "digital_page", "scan_page", "logical_page", "unknown"]).nullable().optional(),
  note_bn: z.string().nullable().optional(),
});

const compositionSourceSchema = z.object({
  label_bn: z.string(),
  url: z.string().url().nullable().optional(),
  page_label_bn: z.string().nullable().optional(),
  note_bn: z.string().nullable().optional(),
});

const poets = defineCollection({
  loader: glob({ pattern: "**/*.json", base: "./src/data/poets" }),
  schema: z.object({
    id: z.string(),
    name_bn: z.string(),
    name_en: z.string(),
    born: z.number().nullable(),
    died: z.number().nullable(),
    pd_status: z.string(),
    bio_bn: z.string(),
    phases: z.array(phaseSchema),
    strands: z.array(phaseSchema).optional(),
  }),
});

const poems = defineCollection({
  loader: glob({ pattern: "**/*.json", base: "./src/data/poems" }),
  schema: z.object({
    id: z.string(),
    poet_id: z.string(),
    phase_id: z.string(),
    title_bn: z.string(),
    body_bn: z.string(),
    tags: z.array(z.string()),
    source_edition: z.string(),
    source_year: z.number().nullable(),
    source_name_bn: z.string().optional(),
    source_url: z.string().url().optional(),
    isbn: z.string().nullable().optional(),
    wikisource_url: z.string().url().nullable().optional(),
    marketplace_url: z.string().url().nullable().optional(),
    purchase_links: z.array(purchaseLinkSchema).optional(),
    book_sources: z.array(bookSourceSchema).optional(),
    composition_date_bn: z.string().nullable().optional(),
    composition_place_bn: z.string().nullable().optional(),
    composition_note_bn: z.string().nullable().optional(),
    composition_sources: z.array(compositionSourceSchema).optional(),
    sort_order: z.number().optional(),
    verified: z.boolean(),
  }),
});

const tags = defineCollection({
  loader: glob({ pattern: "**/*.json", base: "./src/data/tags" }),
  schema: z.object({
    id: z.string(),
    label_bn: z.string(),
    label_en: z.string(),
  }),
});

export const collections = { poets, poems, tags };
