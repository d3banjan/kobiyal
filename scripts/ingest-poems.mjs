import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const datasetRoot = process.env.DATASET_ROOT ?? "/scratch/Bengali-Poem-Dataset";
const outputRoot = process.env.OUTPUT_ROOT ?? "src/data/poems";

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
  ["bangla-kobita.com", "বাংলা কবিতা"]
];

const selections = [
  {
    id: "nilima",
    poet_bn: "জীবনানন্দ দাশ",
    poet_id: "jibanananda-das",
    phase_id: "jhara-palak",
    title_bn: "নীলিমা",
    source_edition: "ঝরা পালক",
    source_year: 1927,
    tags: ["আকাশ", "স্বপ্ন"],
    dir: "নীলিমা"
  },
  {
    id: "kuri-bochor-pore",
    poet_bn: "জীবনানন্দ দাশ",
    poet_id: "jibanananda-das",
    phase_id: "dhusar-pandulipi",
    title_bn: "কুড়ি বছর পরে",
    source_edition: "ধূসর পাণ্ডুলিপি",
    source_year: 1936,
    tags: ["সময়", "স্মৃতি", "পথ"],
    dir: "কুড়ি বছর পরে"
  },
  {
    id: "banalata-sen",
    poet_bn: "জীবনানন্দ দাশ",
    poet_id: "jibanananda-das",
    phase_id: "banalata-sen",
    title_bn: "বনলতা সেন",
    source_edition: "বনলতা সেন",
    source_year: 1942,
    tags: ["অন্ধকার", "পথ", "শান্তি"],
    dir: "বনলতা সেন"
  },
  {
    id: "adbhut-andhar-ek",
    poet_bn: "জীবনানন্দ দাশ",
    poet_id: "jibanananda-das",
    phase_id: "mahaprithibi-timir",
    title_bn: "অদ্ভুত আঁধার এক",
    source_edition: "মহাপৃথিবী",
    source_year: 1944,
    tags: ["অন্ধকার", "মৃত্যু", "সময়"],
    dir: "অদ্ভুত আঁধার এক"
  },
  {
    id: "abar-asibo-phire",
    poet_bn: "জীবনানন্দ দাশ",
    poet_id: "jibanananda-das",
    phase_id: "rupasi-bangla",
    title_bn: "আবার আসিব ফিরে",
    source_edition: "রূপসী বাংলা",
    source_year: 1957,
    tags: ["বাংলা", "নদী", "ফিরে-আসা"],
    dir: "আবার আসিব ফিরে"
  },
  {
    id: "sonar-tori",
    poet_bn: "রবীন্দ্রনাথ ঠাকুর",
    poet_id: "rabindranath-tagore",
    phase_id: "tagore-sonar-tori",
    title_bn: "সোনার তরী",
    source_edition: "সোনার তরী",
    source_year: 1894,
    tags: ["নদী", "যাত্রা", "ফসল"],
    dir: "সোনার তরী"
  },
  {
    id: "he-mahajibn",
    poet_bn: "সুকান্ত ভট্টাচার্য",
    poet_id: "sukanta-bhattacharya",
    phase_id: "sukanta-charpatra",
    title_bn: "হে মহাজীবন",
    source_edition: "ছাড়পত্র",
    source_year: 1948,
    tags: ["ক্ষুধা", "সংগ্রাম", "সময়"],
    dir: "হে মহাজীবন"
  }
];

function assertSafeSelection(selection) {
  if (excludedPoets.has(selection.poet_bn)) {
    throw new Error(`Excluded poet selected: ${selection.poet_bn}`);
  }
  if (!allowedPoets.has(selection.poet_bn)) {
    throw new Error(`Poet is not allowlisted: ${selection.poet_bn}`);
  }
}

async function readSource(selection) {
  const sourcePath = path.join(datasetRoot, "dataset", selection.poet_bn, selection.dir, "SOURCE.txt");
  try {
    const source = await readFile(sourcePath, "utf8");
    const firstLine = source.trim().split(/\r?\n/)[0];
    if (!firstLine) return {};
    const name = sourceNames.find(([host]) => firstLine.includes(host))?.[1];
    return { source_url: firstLine, source_name_bn: name };
  } catch {
    return {};
  }
}

async function readBody(selection) {
  const bodyPath = path.join(datasetRoot, "dataset", selection.poet_bn, selection.dir, `${selection.dir}.txt`);
  const body = await readFile(bodyPath, "utf8");
  return body.replace(/\r\n/g, "\n").trim();
}

await mkdir(outputRoot, { recursive: true });

for (const selection of selections) {
  assertSafeSelection(selection);
  const body_bn = await readBody(selection);
  const source = await readSource(selection);
  const poem = {
    id: selection.id,
    poet_id: selection.poet_id,
    phase_id: selection.phase_id,
    title_bn: selection.title_bn,
    body_bn,
    tags: selection.tags,
    source_edition: selection.source_edition,
    source_year: selection.source_year,
    ...(source.source_name_bn ? { source_name_bn: source.source_name_bn } : {}),
    ...(source.source_url ? { source_url: source.source_url } : {}),
    verified: false
  };

  await writeFile(path.join(outputRoot, `${selection.id}.json`), `${JSON.stringify(poem, null, 2)}\n`);
}
