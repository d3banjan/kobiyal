import strings from "../data/strings-bn.json";

const digits = "০১২৩৪৫৬৭৮৯";

export const S = strings;

export function bn(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\d/g, (digit) => digits[Number(digit)]);
}

export function interpolate(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce((result, [key, value]) => {
    return result.replaceAll(`{${key}}`, bn(value));
  }, template);
}

export function withBase(path: string): string {
  const base = import.meta.env.BASE_URL;
  const cleanPath = path.startsWith("/") ? path.slice(1) : path;
  return `${base}${cleanPath}`;
}
