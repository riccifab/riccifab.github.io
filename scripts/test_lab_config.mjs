import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  CANONICAL_LABS,
  CUSTOM_LAB_VALUE,
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
assert.equal(CUSTOM_LAB_VALUE, "__other__");
const iurilliAliases = legacyLabAliases("Iurilli");
assert.ok(iurilliAliases.includes("IURILLI"));
assert.ok(iurilliAliases.includes("Iurilli Lab"));
assert.ok(iurilliAliases.includes("Iurilli-lab"));

const ticketsHtml = await readFile(new URL("../tickets.html", import.meta.url), "utf8");
assert.match(ticketsHtml, /<select id="tLab" required>/);
assert.match(ticketsHtml, /id="tLabOther"[^>]+disabled/);
assert.doesNotMatch(ticketsHtml, /<input id="tLab"/);

console.log("Lab selector checks passed.");
