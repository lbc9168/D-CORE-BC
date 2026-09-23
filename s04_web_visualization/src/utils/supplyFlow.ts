import { ISO3_TO_REGION } from "./continentMapping.ts";

/** Event roles are retained across milestones; a later EPC role must not erase supply. */
export interface SupplyEvent {
  projectId: string;
  eventId: string;
  supplier: string;
  supplierEn: string;
  year: number;
}

export interface SupplyProject {
  project_id: string;
  category: string;
  country_iso: string;
  country: string;
  country_en?: string;
}

export interface FlowNode { id: string; label: string; value: number }
export interface FlowLink { source: string; target: string; value: number }
export type DestinationMode = "country" | "subregion" | "continent";
export interface SupplyFlow {
  destinationMode: DestinationMode;
  suppliers: FlowNode[];
  destinations: FlowNode[];
  links: FlowLink[];
  projectCount: number;
  supplierCount: number;
  relationshipCount: number;
  shownCount: number;
}

/** RFC-style CSV: preserve commas/newlines in quoted fields and reject broken exports. */
function csvRows(input: string): string[][] {
  const text = input.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let row: string[] = [], field = "", quoted = false, closed = false;
  const endField = () => { row.push(field); field = ""; closed = false; };
  const endRow = () => { endField(); if (row.some(Boolean)) rows.push(row); row = []; };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else { quoted = false; closed = true; }
      } else field += c;
    } else if (c === ",") endField();
    else if (c === "\n" || c === "\r") {
      endRow();
      if (c === "\r" && text[i + 1] === "\n") i++;
    } else if (c === '"' && field === "" && !closed) quoted = true;
    else {
      if (closed || c === '"') throw new Error("Invalid quoting in event CSV.");
      field += c;
    }
  }
  if (quoted) throw new Error("Incomplete event CSV.");
  if (field || row.length || closed) endRow();
  return rows;
}

/** Validate the public export boundary, then retain only explicitly recorded suppliers. */
export function parseSupplyEvents(csv: string): SupplyEvent[] {
  const [header, ...rows] = csvRows(csv);
  const required = ["canonical_id", "event_id", "entity_cn", "entity_en", "year", "involvement_type_cn"];
  if (!header || new Set(header).size !== header.length || required.some(k => !header.includes(k))) {
    throw new Error("The event export is missing required supply fields.");
  }
  const col = Object.fromEntries(required.map(k => [k, header.indexOf(k)]));
  const events: SupplyEvent[] = [];
  for (const [index, row] of rows.entries()) {
    if (row.length !== header.length) throw new Error(`Invalid event CSV row ${index + 2}.`);
    if (row[col.involvement_type_cn].trim() !== "设备供应商") continue;
    const projectId = row[col.canonical_id].trim(), eventId = row[col.event_id].trim();
    const supplier = row[col.entity_cn].trim(), rawYear = row[col.year].trim();
    if (!projectId || !eventId || !supplier || !/^\d{4}$/.test(rawYear) || +rawYear < 1900 || +rawYear > 2100) {
      throw new Error(`Invalid supplier identity or year at CSV row ${index + 2}.`);
    }
    events.push({ projectId, eventId, supplier, supplierEn: row[col.entity_en].trim(), year: +rawYear });
  }
  return events;
}

const compare = (a: FlowNode, b: FlowNode) => b.value - a.value || a.id.localeCompare(b.id, "en");
const UNSPECIFIED = "__unspecified", MULTIPLE = "__multiple", OTHER = "__other";

function destination(p: SupplyProject): string {
  // Some multi-country records have a single ISO code; never assign the entire record to it.
  if (/[、,，;；]/.test(p.country)) return MULTIPLE;
  const iso = p.country_iso.toUpperCase();
  return Object.prototype.hasOwnProperty.call(ISO3_TO_REGION, iso) ? iso : UNSPECIFIED;
}

/** Projects must already satisfy the website baseline. Tabs use primary project category.
 * Each project/supplier contributes one unit, regardless of event count or project capacity.
 */
export function buildSupplyFlow(
  events: SupplyEvent[], projects: SupplyProject[], category: string, topN: 5 | 10,
  destinationMode: DestinationMode = "country",
): SupplyFlow {
  const eligible = new Map(projects.filter(p => p.category === category).map(p => [p.project_id, p]));
  const countryNames = new Map<string, Map<string, number>>();
  for (const p of projects) {
    if (!p.country_en) continue;
    const key = destination(p), counts = countryNames.get(key) ?? new Map<string, number>();
    counts.set(p.country_en, (counts.get(p.country_en) ?? 0) + 1);
    countryNames.set(key, counts);
  }
  const destinationLabel = (id: string): string => {
    if (id === UNSPECIFIED) return "Destination unspecified";
    if (id === MULTIPLE) return "Multiple countries";
    if (id === OTHER) return destinationMode === "country" ? "Other countries" : `Other ${destinationMode}s`;
    if (destinationMode !== "country") return id;
    return [...(countryNames.get(id) ?? [])].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "en"))[0]?.[0] ?? id;
  };
  const seen = new Set<string>(), projectIds = new Set<string>();
  const suppliers = new Map<string, FlowNode>();
  const pairs: Array<{ source: string; target: string }> = [];
  // Stable ordering also chooses one reproducible English label if events differ.
  for (const e of [...events].sort((a, b) => a.eventId.localeCompare(b.eventId, "en") || a.supplier.localeCompare(b.supplier, "en") || a.supplierEn.localeCompare(b.supplierEn, "en"))) {
    const p = eligible.get(e.projectId);
    if (!p || e.year < 2022) continue;
    const key = JSON.stringify([e.projectId, e.supplier]);
    if (seen.has(key)) continue;
    seen.add(key); projectIds.add(e.projectId);
    const node = suppliers.get(e.supplier) ?? { id: e.supplier, label: e.supplierEn || e.supplier, value: 0 };
    node.value++; suppliers.set(e.supplier, node);
    const country = destination(p);
    // Aggregate original country relationships before top-destination reduction, so
    // countries hidden under "Other" still reach their own subregion or continent.
    const target = destinationMode !== "country" && !country.startsWith("__")
      ? ISO3_TO_REGION[country][destinationMode] : country;
    pairs.push({ source: e.supplier, target });
  }
  const top = [...suppliers.values()].sort(compare).slice(0, topN);
  const selected = new Set(top.map(s => s.id));
  const shown = pairs.filter(p => selected.has(p.source));
  const counts = new Map<string, number>();
  for (const p of shown) counts.set(p.target, (counts.get(p.target) ?? 0) + 1);
  const topDestinations = new Set([...counts].filter(([id]) => !id.startsWith("__"))
    .map(([id, value]) => ({ id, value, label: destinationLabel(id) })).sort(compare).slice(0, 8).map(n => n.id));
  const linkMap = new Map<string, FlowLink>(), targets = new Map<string, FlowNode>();
  for (const p of shown) {
    const target = topDestinations.has(p.target) || p.target.startsWith("__") ? p.target : OTHER;
    const key = JSON.stringify([p.source, target]);
    const link = linkMap.get(key) ?? { source: p.source, target, value: 0 };
    link.value++; linkMap.set(key, link);
    const node = targets.get(target) ?? { id: target, label: destinationLabel(target), value: 0 };
    node.value++; targets.set(target, node);
  }
  return {
    destinationMode,
    suppliers: top,
    destinations: [...targets.values()].sort((a, b) => Number(a.id.startsWith("__")) - Number(b.id.startsWith("__")) || compare(a, b)),
    links: [...linkMap.values()], projectCount: projectIds.size, supplierCount: suppliers.size,
    relationshipCount: pairs.length, shownCount: shown.length,
  };
}
