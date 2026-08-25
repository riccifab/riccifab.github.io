import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  CANONICAL_LABS,
  LAB_OPTIONS,
  OTHER_LAB_NAME,
  canonicalizeLabName,
  legacyLabAliases,
  normalizeLabKey,
} from "../lab-config.mjs";

const variants = ["Iurilli", "iurilli", "IURILLI", " Iurilli Lab ", "Iurilli-lab"];
assert.deepEqual(new Set(variants.map(normalizeLabKey)), new Set(["iurilli"]));
assert.deepEqual(new Set(variants.map(canonicalizeLabName)), new Set(["Iurilli"]));
assert.equal(canonicalizeLabName("  Advanced   Imaging  "), "Advanced Imaging");
assert.equal(normalizeLabKey("Müller Lab"), "muller");
assert.deepEqual(CANONICAL_LABS, ["Gozzi", "Iurilli", "Lombardo", "Rossi"]);
assert.equal(OTHER_LAB_NAME, "Other");
assert.deepEqual(LAB_OPTIONS, ["Gozzi", "Iurilli", "Lombardo", "Rossi", "Other"]);
assert.equal(canonicalizeLabName("other"), "Other");
const iurilliAliases = legacyLabAliases("Iurilli");
assert.ok(iurilliAliases.includes("IURILLI"));
assert.ok(iurilliAliases.includes("Iurilli Lab"));
assert.ok(iurilliAliases.includes("Iurilli-lab"));

const ticketsHtml = await readFile(new URL("../tickets.html", import.meta.url), "utf8");
assert.match(ticketsHtml, /<select id="tLab" required>/);
for (const lab of LAB_OPTIONS) {
  assert.match(ticketsHtml, new RegExp(`<option value="${lab}">${lab}</option>`));
}
assert.doesNotMatch(ticketsHtml, /<input id="tLab"/);
assert.doesNotMatch(ticketsHtml, /id="tLabOther"/);
assert.doesNotMatch(ticketsHtml, />Other lab</);

console.log("Lab selector checks passed.");
