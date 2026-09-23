import { useState } from "react";
import type { FlowNode, SupplyFlow } from "@/utils/supplyFlow";

const COLORS = ["#2F6562", "#5787ad", "#b78039", "#8174a3", "#6b956c", "#af6d7c", "#4f929c", "#93816d", "#687da5", "#9c8c40"];

function shortLabel(label: string): string {
  return label.replace(/,?\s+(Co\.,?\s*Ltd\.?|Corporation|Inc\.|Holdings Co\., Ltd\.)$/i, "");
}

/** Two-column Sankey with one shared scale: every band conserves project/supplier counts. */
export default function SupplySankey({ flow }: { flow: SupplyFlow }) {
  const [active, setActive] = useState<{ source?: string; target?: string } | null>(null);
  const gap = 30, top = 48, height = Math.max(490, Math.max(flow.suppliers.length, flow.destinations.length) * 54);
  const innerHeight = height - top - 28;
  const scale = (innerHeight - gap * (Math.max(flow.suppliers.length, flow.destinations.length) - 1)) / flow.shownCount;
  const position = (nodes: FlowNode[]) => {
    let y = top + (innerHeight - flow.shownCount * scale - gap * (nodes.length - 1)) / 2;
    return nodes.map(n => { const result = { ...n, y, h: n.value * scale }; y += result.h + gap; return result; });
  };
  const suppliers = position(flow.suppliers), destinations = position(flow.destinations);
  const offsets = new Map<string, number>();
  const paths = suppliers.flatMap((s, i) => destinations.flatMap(d => {
    const link = flow.links.find(l => l.source === s.id && l.target === d.id);
    if (!link) return [];
    const sy = s.y + (offsets.get(`s:${s.id}`) ?? 0), ty = d.y + (offsets.get(`t:${d.id}`) ?? 0);
    const h = link.value * scale;
    offsets.set(`s:${s.id}`, sy - s.y + h); offsets.set(`t:${d.id}`, ty - d.y + h);
    return [{ ...link, sy, ty, h, color: COLORS[i], sourceLabel: s.label, targetLabel: d.label,
      share: Math.round(link.value / s.value * 100),
      d: `M 248 ${sy} C 410 ${sy}, 490 ${ty}, 652 ${ty} L 652 ${ty + h} C 490 ${ty + h}, 410 ${sy + h}, 248 ${sy + h} Z` }];
  }));
  const selectedPaths = active ? paths.filter(p => (!active.source || p.source === active.source) && (!active.target || p.target === active.target)) : [];
  const description = active
    ? `${suppliers.find(s => s.id === active.source)?.label ?? "All shown suppliers"} → ${destinations.find(d => d.id === active.target)?.label ?? "All destinations"}: ${selectedPaths.reduce((sum, p) => sum + p.value, 0)} project–supplier relationships${active.source && active.target ? ` · ${selectedPaths[0]?.share ?? 0}% of this supplier’s recorded projects` : ""}`
    : "Hover or focus to explore. Click a supplier, destination or flow to keep it highlighted.";
  const toggle = (next: { source?: string; target?: string }) => setActive(old => old?.source === next.source && old?.target === next.target ? null : next);
  const keyAction = (e: React.KeyboardEvent, next: { source?: string; target?: string }) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(next); }
    if (e.key === "Escape") setActive(null);
  };

  return <>
    <p className="mb-2 text-xs text-slate-400 md:hidden">Scroll horizontally to explore the full chart.</p>
    <div className="overflow-x-auto" aria-label="Equipment supply Sankey chart">
      <svg viewBox={`0 0 900 ${height}`} className="w-full min-w-[780px]" role="group" aria-label={`Suppliers flow to destination ${flow.destinationMode === "country" ? "countries" : `${flow.destinationMode}s`}; band width counts project–supplier relationships.`}>
        <text x="232" y="20" textAnchor="end" className="fill-slate-400 text-[11px] font-semibold tracking-wider">EQUIPMENT SUPPLIERS</text>
        <text x="668" y="20" className="fill-slate-400 text-[11px] font-semibold tracking-wider">{flow.destinationMode === "country" ? "COUNTRIES" : `${flow.destinationMode.toUpperCase()}S`}</text>
        {paths.map(p => {
          const selected = !active || ((!active.source || p.source === active.source) && (!active.target || p.target === active.target));
          const label = `${p.sourceLabel} → ${p.targetLabel}: ${p.value} project–supplier relationships; ${p.share}% of supplier total`;
          return <path key={`${p.source}:${p.target}`} d={p.d} fill={p.color} fillOpacity={selected ? .42 : .06}
            className="cursor-pointer hover:[fill-opacity:0.75] focus:outline-none focus:stroke-slate-700" strokeWidth={1.5}
            tabIndex={0} role="button" aria-label={label} aria-pressed={!!active && selected}
            onClick={() => toggle({ source: p.source, target: p.target })}
            onKeyDown={e => keyAction(e, { source: p.source, target: p.target })}>
            <title>{label}</title>
          </path>;
        })}
        {suppliers.map((s, i) => <g key={s.id} role="button" tabIndex={0} aria-label={`${s.label}: ${s.value} recorded projects`}
          aria-pressed={active?.source === s.id} className="cursor-pointer focus:outline-none focus:underline"
          onClick={() => toggle({ source: s.id })} onKeyDown={e => keyAction(e, { source: s.id })}>
          <title>{s.label}: {s.value} recorded projects</title>
          <rect x="236" y={s.y} width="12" height={s.h} fill={COLORS[i]} />
          <foreignObject x="0" y={s.y + s.h / 2 - 25} width="225" height="50">
            <div className="h-full flex flex-col justify-center text-right text-[12px] leading-tight text-slate-700">
              <span className="line-clamp-2 font-medium">{shortLabel(s.label)}</span>
              <span className="text-slate-400 mt-0.5">{s.value} projects</span>
            </div>
          </foreignObject>
        </g>)}
        {destinations.map(d => <g key={d.id} role="button" tabIndex={0} aria-label={`${d.label}: ${d.value} project–supplier relationships`}
          aria-pressed={active?.target === d.id} className="cursor-pointer focus:outline-none focus:underline"
          onClick={() => toggle({ target: d.id })} onKeyDown={e => keyAction(e, { target: d.id })}>
          <title>{d.label}: {d.value} project–supplier relationships</title>
          <rect x="652" y={d.y} width="12" height={d.h} fill="#94a3b8" />
          <text x="678" y={d.y + d.h / 2 - 3} className="fill-slate-700 text-[12px] font-medium">{d.label}</text>
          <text x="678" y={d.y + d.h / 2 + 14} className="fill-slate-400 text-[11px]">{d.value} relationships</text>
        </g>)}
      </svg>
    </div>
    <div className="mt-3 min-h-10 flex items-start justify-between gap-3 text-xs text-slate-500" aria-live="polite">
      <p>{description}</p>
      {active && <button type="button" className="shrink-0 text-brand-600 underline" onClick={() => setActive(null)}>Clear selection</button>}
    </div>
  </>;
}
