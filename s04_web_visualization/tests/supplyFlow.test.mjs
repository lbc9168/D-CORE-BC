import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { buildSupplyFlow, parseSupplyEvents } from "../src/utils/supplyFlow.ts";

const header = "canonical_id,event_id,entity_cn,entity_en,year,involvement_type_cn";
const event = (projectId, supplier, year = 2024, eventId = `${projectId}-01`) => ({ projectId, supplier, supplierEn: supplier, year, eventId });
const project = (project_id, iso = "SAU", country = "沙特", category = "光伏") => ({ project_id, category, country_iso: iso, country, country_en: iso === "SAU" ? "Saudi Arabia" : iso });

test("CSV handles BOM, CRLF, quoted commas, quotes and embedded newlines", () => {
  const events = parseSupplyEvents(`\uFEFF${header}\r\np1,e1,公司,"Supplier, \"\"A\"\"\nLtd",2024,设备供应商\r\np2,e2,其他,Other,2023,EPC总承包\r\n`);
  assert.equal(events.length, 1);
  assert.equal(events[0].supplierEn, 'Supplier, "A"\nLtd');
  assert.equal(events[0].projectId, "p1");
});

test("invalid input fails visibly instead of producing partial totals", () => {
  for (const csv of ["<html>Error</html>", `${header}\np1,e1,A,A,2024`, `${header}\np1,e1,A,A,2024x,设备供应商`, `${header}\np1,e1,,A,2024,设备供应商`, `${header}\np1,e1,A,\"broken,2024,设备供应商`]) {
    assert.throws(() => parseSupplyEvents(csv));
  }
  assert.deepEqual(parseSupplyEvents(header), []);
});

test("deduplicate supplier/project across events, retaining co-suppliers and enforcing year/category/baseline", () => {
  const events = [event("p1", "A"), event("p1", "A", 2025, "p1-02"), event("p1", "B"), event("p2", "A"), event("p3", "A", 2021), event("p4", "A"), event("hidden", "A")];
  const projects = [project("p1"), project("p2"), project("p3"), project("p4", "GBR", "英国", "储能")];
  const flow = buildSupplyFlow(events, projects, "光伏", 5);
  assert.equal(flow.projectCount, 2);
  assert.equal(flow.relationshipCount, 3);
  assert.equal(flow.suppliers.find(s => s.id === "A").value, 2);
  assert.equal(flow.destinations[0].value, 3);
  assert.equal(buildSupplyFlow(events, projects, "储能", 5).projectCount, 1);
});

test("country aliases merge; a multi-country record is not assigned to its first ISO", () => {
  const projects = [project("a"), project("b", "SAU", "沙特阿拉伯"), project("c", "DNK", "丹麦、瑞典、芬兰、挪威"), project("d", "UNK", "东欧")];
  const flow = buildSupplyFlow(projects.map(p => event(p.project_id, "A")), projects, "光伏", 5);
  assert.equal(flow.destinations.find(d => d.id === "SAU").value, 2);
  assert.equal(flow.destinations.find(d => d.label === "Multiple countries").value, 1);
  assert.equal(flow.destinations.find(d => d.label === "Destination unspecified").value, 1);
  assert.ok(!flow.destinations.some(d => d.id === "DNK"));
});

test("top supplier/destination reduction conserves every shown relationship", () => {
  const countries = ["SAU", "GBR", "USA", "AUS", "FRA", "DEU", "BRA", "IND", "ZAF", "JPN", "KOR"];
  const projects = countries.map((iso, i) => project(`p${i}`, iso));
  const events = projects.flatMap(p => Array.from({ length: 12 }, (_, i) => event(p.project_id, `Supplier ${i}`)));
  for (const n of [5, 10]) {
    const flow = buildSupplyFlow(events, projects, "光伏", n);
    assert.equal(flow.suppliers.length, n);
    assert.equal(flow.shownCount, n * countries.length);
    assert.equal(flow.destinations.find(d => d.label === "Other countries").value, n * 3);
    assert.equal(flow.links.reduce((sum, l) => sum + l.value, 0), flow.shownCount);
    for (const s of flow.suppliers) assert.equal(flow.links.filter(l => l.source === s.id).reduce((sum, l) => sum + l.value, 0), s.value);
    for (const d of flow.destinations) assert.equal(flow.links.filter(l => l.target === d.id).reduce((sum, l) => sum + l.value, 0), d.value);
    assert.deepEqual(buildSupplyFlow([...events].reverse(), projects, "光伏", n), flow);
  }
});

test("subregions merge original countries before truncation, preserving ranks and all counts", () => {
  const countries = ["SAU", "ARE", "EGY", "IDN", "VNM", "THA", "MYS", "PHL", "SGP", "BRN", "KHM", "LAO", "MMR"];
  const projects = countries.map((iso, i) => project(`p${i}`, iso));
  projects.push(project("multi", "DNK", "丹麦、瑞典"), project("unknown", "UNK", "未知"));
  const events = projects.flatMap(p => [event(p.project_id, "A"), event(p.project_id, "B"), event(p.project_id, "A", 2025, `${p.project_id}-02`)]);
  const country = buildSupplyFlow(events, projects, "光伏", 5);
  const region = buildSupplyFlow(events, projects, "光伏", 5, "subregion");
  assert.deepEqual(region.suppliers, country.suppliers);
  for (const key of ["projectCount", "supplierCount", "relationshipCount", "shownCount"]) assert.equal(region[key], country[key]);
  assert.equal(region.destinations.find(d => d.label === "South-eastern Asia").value, 20);
  assert.equal(region.destinations.find(d => d.label === "Western Asia").value, 4);
  assert.equal(region.destinations.find(d => d.label === "Northern Africa").value, 2);
  assert.equal(region.destinations.find(d => d.label === "Multiple countries").value, 2);
  assert.equal(region.destinations.find(d => d.label === "Destination unspecified").value, 2);
  assert.ok(country.destinations.some(d => d.label === "Other countries"));
  assert.ok(!region.destinations.some(d => d.label.startsWith("Other")));
  for (const s of region.suppliers) assert.equal(region.links.filter(l => l.source === s.id).reduce((sum, l) => sum + l.value, 0), s.value);
  for (const d of region.destinations) assert.equal(region.links.filter(l => l.target === d.id).reduce((sum, l) => sum + l.value, 0), d.value);
  assert.equal(buildSupplyFlow([], projects, "光伏", 5, "subregion").shownCount, 0);
});

test("continent mapping merges countries across subregions and preserves ambiguous destinations", () => {
  const countries = ["SAU", "IDN", "EGY", "ZAF", "FRA", "DEU", "CAN", "BRA", "AUS", "NZL"];
  const projects = countries.map((iso, i) => project(`p${i}`, iso));
  projects.push(project("multi", "DNK", "丹麦、瑞典"), project("unknown", "UNK", "未知"));
  const events = projects.map(p => event(p.project_id, "A"));
  const flow = buildSupplyFlow(events, projects, "光伏", 5, "continent");
  assert.equal(flow.destinationMode, "continent");
  for (const name of ["Asia", "Africa", "Europe", "Americas", "Oceania"]) {
    assert.equal(flow.destinations.find(d => d.label === name).value, 2);
  }
  assert.equal(flow.destinations.find(d => d.label === "Multiple countries").value, 1);
  assert.equal(flow.destinations.find(d => d.label === "Destination unspecified").value, 1);
  assert.equal(flow.shownCount, 12);
  assert.equal(flow.links.reduce((n, l) => n + l.value, 0), 12);
  assert.ok(!flow.destinations.some(d => d.label.startsWith("Other")));
});

test("current public export: event-only supplier role survives and source files stay unchanged", () => {
  const paths = ["../public/data/events_export.csv", "../public/data/projects_flat.json"];
  const buffers = paths.map(p => readFileSync(new URL(p, import.meta.url)));
  const hashes = buffers.map(b => createHash("sha256").update(b).digest("hex"));
  const events = parseSupplyEvents(buffers[0].toString("utf8"));
  const raw = JSON.parse(buffers[1]);
  const cats = JSON.parse(readFileSync(new URL("../../enums_mapping.json", import.meta.url))).category;
  const unknown = new Set(["", "unknown", "未知", "未披露", "n/a", "na", "-", "—"]);
  const projects = raw.filter(p => !["CHN", "HKG", "MAC", "TWN"].includes(p.country_iso) && p.first_event_year != null && p.last_event_year >= 2022 && !cats[p.category].hidden && !cats[p.category].excluded && !unknown.has((p.primary_entity || "").trim().toLowerCase()) && (cats[p.category].no_capacity || p.capacity_mw != null));
  for (const category of ["光伏", "风电", "水电", "储能"]) {
    const flow = buildSupplyFlow(events, projects, category, 5);
    const ids = new Set(projects.filter(p => p.category === category).map(p => p.project_id));
    const pairs = new Set(events.filter(e => ids.has(e.projectId) && e.year >= 2022).map(e => JSON.stringify([e.projectId, e.supplier])));
    assert.equal(flow.relationshipCount, pairs.size);
    for (const mode of ["subregion", "continent"]) {
      const region = buildSupplyFlow(events, projects, category, 5, mode);
      assert.deepEqual(region.suppliers, flow.suppliers);
      for (const key of ["projectCount", "supplierCount", "relationshipCount", "shownCount"]) assert.equal(region[key], flow[key]);
      assert.equal(region.links.reduce((sum, l) => sum + l.value, 0), flow.shownCount);
    }
    console.log(`${category}: ${flow.projectCount} projects / ${flow.relationshipCount} relationships / ${flow.suppliers.map(s => `${s.id}=${s.value}`).join(", ")}`);
  }
  // This real project records SANY as a supplier in an earlier event, absent in its flat role.
  const p = projects.find(p => p.project_id === "UZB-WD-0007");
  if (p) {
    const flow = buildSupplyFlow(events, [p], "风电", 10);
    assert.ok(flow.suppliers.some(s => s.id === "三一集团有限公司"));
  }
  assert.deepEqual(paths.map(p => createHash("sha256").update(readFileSync(new URL(p, import.meta.url))).digest("hex")), hashes);
});
