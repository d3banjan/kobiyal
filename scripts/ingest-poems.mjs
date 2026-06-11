import { mkdir, readFile, readdir, unlink, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

const datasetRoot = process.env.DATASET_ROOT
  ?? (existsSync("/scratch/Bengali-Poem-Dataset") ? "/scratch/Bengali-Poem-Dataset" : "/tmp/Bengali-Poem-Dataset");
const outputRoot = process.env.OUTPUT_ROOT ?? "src/data/poems";
const poetDir = path.join(datasetRoot, "dataset", "জীবনানন্দ দাশ");

const allowedPoets = new Set([
  "রবীন্দ্রনাথ ঠাকুর",
  "জীবনানন্দ দাশ",
  "সুকান্ত ভট্টাচার্য",
  "মাইকেল মধুসূদন দত্ত",
  "সত্যেন্দ্রনাথ দত্ত",
  "দ্বিজেন্দ্রলাল রায়",
  "কামিনী রায়",
  "চণ্ডীদাস",
  "রামপ্রসাদ সেন"
]);

const excludedPoets = new Set(["কাজী নজরুল ইসলাম", "জসীম উদ্‌দীন", "জসীমউদ্‌দীন"]);

const sourceNames = [
  ["banglarkobita.com", "বাংলার কবিতা"],
  ["bangla-kobita.com", "বাংলা কবিতা"],
  ["banglapoems.wordpress.com", "বাংলা পোয়েমস"],
  ["kobita.banglakosh.com", "বাংলাকোষ"]
];

const knownPoems = new Map([
  ["নীলিমা", {
    id: "nilima",
    phase_id: "jhara-palak",
    source_edition: "ঝরা পালক",
    source_year: 1927,
    tags: ["আকাশ", "স্বপ্ন"]
  }],
  ["কুড়ি বছর পরে", {
    id: "kuri-bochor-pore",
    phase_id: "dhusar-pandulipi",
    source_edition: "ধূসর পাণ্ডুলিপি",
    source_year: 1936,
    tags: ["সময়", "স্মৃতি", "পথ"]
  }],
  ["বনলতা সেন", {
    id: "banalata-sen",
    phase_id: "banalata-sen",
    source_edition: "বনলতা সেন",
    source_year: 1942,
    tags: ["অন্ধকার", "পথ", "শান্তি"]
  }],
  ["অদ্ভুত আঁধার এক", {
    id: "adbhut-andhar-ek",
    phase_id: "mahaprithibi-timir",
    source_edition: "মহাপৃথিবী",
    source_year: 1944,
    tags: ["অন্ধকার", "মৃত্যু", "সময়"]
  }],
  ["আবার আসিব ফিরে", {
    id: "abar-asibo-phire",
    phase_id: "rupasi-bangla",
    source_edition: "রূপসী বাংলা",
    source_year: 1957,
    tags: ["বাংলা", "নদী", "ফিরে-আসা"]
  }]
]);

const classTags = new Set([
  "চিন্তামূলক",
  "প্রেমমূলক",
  "সনেট",
  "প্রকৃতিমূলক",
  "মানবতাবাদী",
  "রূপক",
  "ভক্তিমূলক",
  "স্বদেশমূলক",
  "শোকমূলক"
]);

const tagRules = [
  ["ফিরে-আসা", /ফিরে|আসিব|ফিরিয়া|আবার/],
  ["বাংলা", /বাংলা|বাংলার|বাঙালী|বাঙালি|ধানসিড়ি|ধানসিঁড়ি|জলসিড়ি/],
  ["নদী", /নদী|জল|সাগর|সমুদ্র|ঢেউ|ধানসিড়ি|ধানসিঁড়ি|জলসিড়ি|কীর্তিনাশা/],
  ["আকাশ", /আকাশ|নক্ষত্র|তারা|চাঁদ|সূর্য|রৌদ্র|নীলিমা|মেঘ/],
  ["অন্ধকার", /অন্ধকার|তিমির|রাত্রি|রাত|নিশীথ|ছায়া|কুয়াশা/],
  ["মৃত্যু", /মৃত্যু|মরণ|শব|মরে|মৃত|চিতা|শ্মশান/],
  ["পথ", /পথ|পথে|হাঁটা|চলিয়া|যাত্রী|নাবিক/],
  ["ফসল", /ধান|ফসল|ক্ষেত|খেত|প্রান্তর|নবান্ন/],
  ["শান্তি", /শান্তি|নির্জন|নীরব|ঘুম|অবসর/],
  ["স্মৃতি", /স্মৃতি|মনে হয়|একদিন|বছর|পুরোনো/],
  ["সময়", /সময়|দিন|রাত্রি|বছর|শতাব্দী|কাল/],
  ["স্বপ্ন", /স্বপ্ন|কল্পনা|মায়া/],
  ["সংগ্রাম", /যুদ্ধ|সংগ্রাম|সভ্যতা|মানুষের|মানুষ/],
  ["ক্ষুধা", /ক্ষুধা|ভিখিরী|ভিক্ষু/]
];

const banglaDigits = new Map([
  ["0", "০"],
  ["1", "১"],
  ["2", "২"],
  ["3", "৩"],
  ["4", "৪"],
  ["5", "৫"],
  ["6", "৬"],
  ["7", "৭"],
  ["8", "৮"],
  ["9", "৯"]
]);

function assertSafeSelection(selection) {
  if (excludedPoets.has(selection.poet_bn)) {
    throw new Error(`Excluded poet selected: ${selection.poet_bn}`);
  }
  if (!allowedPoets.has(selection.poet_bn)) {
    throw new Error(`Poet is not allowlisted: ${selection.poet_bn}`);
  }
}

function toBengaliDigits(value) {
  return value.replace(/[0-9]/g, (digit) => banglaDigits.get(digit));
}

function normalizeText(value) {
  return toBengaliDigits(value)
    .replace(/\r\n/g, "\n")
    .replace(/\ufeff/g, "")
    .trim();
}

function isUrl(value) {
  try {
    new URL(value);
    return true;
  } catch {
    return false;
  }
}

async function readSource(dirName) {
  const sourcePath = path.join(poetDir, dirName, "SOURCE.txt");
  try {
    const source = await readFile(sourcePath, "utf8");
    const firstLine = source.trim().split(/\r?\n/)[0]?.trim();
    if (!firstLine || !isUrl(firstLine)) return {};
    const name = sourceNames.find(([host]) => firstLine.includes(host))?.[1];
    return { source_url: firstLine, source_name_bn: name };
  } catch {
    return {};
  }
}

async function readClass(dirName) {
  const classPath = path.join(poetDir, dirName, "CLASS.txt");
  try {
    const value = normalizeText(await readFile(classPath, "utf8"));
    return classTags.has(value) ? value : undefined;
  } catch {
    return undefined;
  }
}

async function readBody(dirName) {
  const bodyPath = path.join(poetDir, dirName, `${dirName}.txt`);
  return normalizeText(await readFile(bodyPath, "utf8"));
}

function poemId(dirName, index) {
  return knownPoems.get(dirName)?.id ?? `jibanananda-${String(index + 1).padStart(3, "0")}`;
}

function tagsFor(dirName, body, classTag) {
  const knownTags = knownPoems.get(dirName)?.tags;
  if (knownTags) return knownTags;

  const tags = classTag ? [classTag] : [];
  const haystack = `${dirName}\n${body}`;
  for (const [tag, pattern] of tagRules) {
    if (pattern.test(haystack) && !tags.includes(tag)) tags.push(tag);
    if (tags.length === 3) break;
  }
  return tags.length ? tags : ["চিন্তামূলক"];
}

async function cleanJibananandaPoems() {
  await mkdir(outputRoot, { recursive: true });
  const files = await readdir(outputRoot);
  await Promise.all(files.map(async (file) => {
    if (!file.endsWith(".json")) return;
    const filePath = path.join(outputRoot, file);
    try {
      const data = JSON.parse(await readFile(filePath, "utf8"));
      if (data.poet_id === "jibanananda-das") await unlink(filePath);
    } catch {
      return;
    }
  }));
}

assertSafeSelection({ poet_bn: "জীবনানন্দ দাশ" });

if (!existsSync(poetDir)) {
  throw new Error(`Dataset not found: ${poetDir}`);
}

await cleanJibananandaPoems();

const dirNames = (await readdir(poetDir, { withFileTypes: true }))
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort((a, b) => a.localeCompare(b, "bn"));

let written = 0;
for (const [index, dirName] of dirNames.entries()) {
  const body_bn = await readBody(dirName);
  if (!body_bn) continue;
  const known = knownPoems.get(dirName);
  const classTag = await readClass(dirName);
  const source = await readSource(dirName);
  const poem = {
    id: poemId(dirName, index),
    poet_id: "jibanananda-das",
    phase_id: known?.phase_id ?? "dataset-archive",
    title_bn: normalizeText(dirName),
    body_bn,
    tags: tagsFor(dirName, body_bn, classTag),
    source_edition: known?.source_edition ?? "সংকলন অজানা",
    source_year: known?.source_year ?? null,
    ...(source.source_name_bn ? { source_name_bn: source.source_name_bn } : {}),
    ...(source.source_url ? { source_url: source.source_url } : {}),
    sort_order: index + 1,
    verified: false
  };

  await writeFile(path.join(outputRoot, `${poem.id}.json`), `${JSON.stringify(poem, null, 2)}\n`);
  written += 1;
}

console.log(`Imported ${written} Jibanananda poems from ${poetDir}.`);
