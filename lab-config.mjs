export const CANONICAL_LABS = Object.freeze([
  "Gozzi",
  "Iurilli",
  "Lombardo",
  "Rossi",
]);

export const CUSTOM_LAB_VALUE = "__other__";

export function cleanLabName(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}

export function normalizeLabKey(value) {
  const normalized = cleanLabName(value)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  const parts = normalized.split(/\s+/).filter(Boolean);
  if (parts.at(-1) === "lab") parts.pop();
  return parts.join("");
}

const CANONICAL_LAB_BY_KEY = new Map(
  CANONICAL_LABS.map((lab) => [normalizeLabKey(lab), lab]),
);

export function canonicalizeLabName(value) {
  const cleaned = cleanLabName(value);
  if (!cleaned) return "";
  return CANONICAL_LAB_BY_KEY.get(normalizeLabKey(cleaned)) || cleaned;
}

export function isCanonicalLab(value) {
  return CANONICAL_LABS.includes(canonicalizeLabName(value));
}

export function legacyLabAliases(value) {
  const canonical = canonicalizeLabName(value);
  if (!CANONICAL_LABS.includes(canonical)) return canonical ? [canonical] : [];

  const lower = canonical.toLowerCase();
  const upper = canonical.toUpperCase();
  return [...new Set([
    canonical,
    lower,
    upper,
    `${canonical} Lab`,
    `${canonical} lab`,
    `${canonical}-lab`,
    `${canonical}_lab`,
    `${lower} lab`,
    `${lower}-lab`,
    `${lower}_lab`,
    `${upper} LAB`,
    `${upper}-LAB`,
    `${upper}_LAB`,
  ])];
}
