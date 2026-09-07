import { useEffect, useMemo, useState } from "react";
import { FlaskConical } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { CATEGORIES } from "@/utils/enums";
import { buildSupplyFlow, parseSupplyEvents, type SupplyEvent, type DestinationMode } from "@/utils/supplyFlow";
import SupplySankey from "@/components/charts/SupplySankey";

const TYPES = ["PV", "WD", "HY", "ES"].map(code => {
  const entry = Object.entries(CATEGORIES).find(([, meta]) => meta.code === code)!;
  return { code, category: entry[0], label: entry[1].en };
});

/** Experimental analyses use their own controls, independent of Map/Table filters. */
export default function LabPage() {
  const { allProjects, loading, error: projectError, lastUpdated } = useData();
  const [type, setType] = useState(TYPES[0]);
  const [topN, setTopN] = useState<5 | 10>(5);
  const [destinationMode, setDestinationMode] = useState<DestinationMode>("country");
  const [events, setEvents] = useState<SupplyEvent[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(""); setEvents(null);
    async function load() {
      try {
        const response = await fetch(`${import.meta.env.BASE_URL}data/events_export.csv`, { signal: controller.signal });
        if (!response.ok) throw new Error(`Event export request failed (${response.status}).`);
        const data = parseSupplyEvents(await response.text());
        if (!controller.signal.aborted) setEvents(data);
      } catch (e: unknown) {
        if (!controller.signal.aborted) {
          console.error("[Lab] Supplier events could not be loaded:", e);
          setError("Supply records could not be loaded. Please try again.");
        }
      }
    }
    void load();
    return () => controller.abort();
  }, [attempt]);
  const flow = useMemo(() => buildSupplyFlow(events ?? [], allProjects, type.category, topN, destinationMode), [events, allProjects, type, topN, destinationMode]);

  return <div className="flex-1 overflow-y-auto bg-slate-50 p-4 md:p-8">
    <div className="max-w-6xl mx-auto">
      <header className="mb-7 flex items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-700"><FlaskConical size={22} className="text-brand-500" />Lab</h1>
          <p className="mt-1.5 text-sm text-slate-500">Experimental views of Chinese overseas renewable energy projects.</p>
        </div>
        <span className="rounded-full bg-brand-100 px-3 py-1 text-xs text-brand-600">Experimental</span>
      </header>
      <label className="block text-xs font-medium text-slate-500 mb-6">Analysis
        <select aria-label="Analysis" className="block mt-2 max-w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-700" value="equipment-supply" onChange={() => {}}>
          <option value="equipment-supply">Equipment Supply Flow Chart</option>
        </select>
      </label>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-medium text-slate-500 mb-2">Project type</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Project type">
            {TYPES.map(t => <button key={t.code} type="button" aria-pressed={type.code === t.code}
              onClick={() => setType(t)} className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${type.code === t.code ? "bg-brand-600 text-white" : "bg-white text-slate-600 border border-slate-200 hover:bg-brand-50"}`}>{t.label}</button>)}
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <p className="text-xs font-medium text-slate-500 mb-2">Destination</p>
            <div className="flex flex-wrap gap-1 rounded-lg border border-slate-200 bg-white p-1" role="group" aria-label="Destination grouping">
              {(["country", "subregion", "continent"] as const).map(mode => <button key={mode} type="button"
                aria-pressed={destinationMode === mode} onClick={() => setDestinationMode(mode)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${destinationMode === mode ? "bg-brand-600 text-white" : "text-slate-600 hover:bg-brand-50"}`}>
                {mode[0].toUpperCase() + mode.slice(1)}
              </button>)}
            </div>
          </div>
          <label className="text-xs text-slate-500">Suppliers
          <select aria-label="Number of suppliers" value={topN} onChange={e => setTopN(e.target.value === "10" ? 10 : 5)} className="ml-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
            <option value={5}>Top 5</option><option value={10}>Top 10</option>
          </select>
          </label>
        </div>
      </div>
      <section className="card p-4 md:p-6" aria-label={`${type.label} equipment supply flows`}>
        <div className="mb-5">
          <h2 className="text-base font-semibold text-slate-700">{type.label} · Equipment supply flows</h2>
          <p className="mt-1 text-xs text-slate-500">Recorded supplier → destination {destinationMode} · Events from 2022 · Data through {lastUpdated || "—"}</p>
        </div>
        {error || projectError ? <div role="alert" className="py-16 text-center text-sm text-slate-600">
          <p>{error || "Project data could not be loaded. Please reload the page."}</p>
          {!projectError && <button type="button" onClick={() => setAttempt(n => n + 1)} className="btn-primary mt-3">Try again</button>}
        </div> : loading || !events ? <p role="status" className="py-20 text-center text-sm text-slate-400">Loading supply records…</p>
          : !flow.shownCount ? <p role="status" className="py-20 text-center text-sm text-slate-500">No equipment supplier relationships recorded for this project type.</p>
          : <>
            <p className="mb-4 text-xs text-slate-500">{flow.projectCount} projects with supplier records · {flow.supplierCount} recorded suppliers · Showing {flow.suppliers.length} suppliers and {flow.shownCount} of {flow.relationshipCount} project–supplier relationships</p>
            <SupplySankey key={`${type.code}-${topN}-${destinationMode}`} flow={flow} />
          </>}
      </section>
      <details className="mt-4 text-xs text-slate-500 leading-relaxed">
        <summary className="cursor-pointer font-medium text-slate-600">How to read this chart</summary>
        <p className="mt-2 max-w-4xl">Only event records explicitly labelled Equipment Supplier are included. Each supplier is counted once per project across events from 2022 onwards, within the website’s eligible overseas projects. Band width counts project–supplier relationships, not shipments, equipment volume, capacity or market share. Multiple suppliers on one project count separately.</p>
        <p className="mt-2 max-w-4xl">Tabs follow the primary project category. Solar and wind projects with storage remain in their own category; equipment components are not distinguished. Suppliers use the recorded company identities, without additional parent-company consolidation. The top eight countries or subregions among shown suppliers are displayed individually; remaining destinations are grouped as Other. Continent shows all represented continents. Multiple-country records and unspecified destinations remain separate in all views. Records may describe awards or contracts as well as completed projects.</p>
        <p className="mt-2 max-w-4xl">Subregion and Continent use the same country mapping as the website’s Region filter: Africa, Americas, Asia, Europe and Oceania. Western Asia and Northern Africa are separate subregions; there is no combined Middle East and North Africa (MENA) group. Switching destination grouping preserves supplier rankings and relationship totals.</p>
      </details>
    </div>
  </div>;
}
