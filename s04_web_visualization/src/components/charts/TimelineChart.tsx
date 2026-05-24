import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { Project, VizGroup } from "@/types";
import { CATEGORIES, VIZ_GROUPS, VIZ_GROUP_KEYS } from "@/types";
import { useLang } from "@/contexts/LanguageContext";

// 事件级里程碑趋势图：按选中 4 大类(tab) × 年份 × 类别堆叠，一个项目每个阶段-年份各计一次
interface TimelineChartProps {
  projects: Project[];   // trendProjects：除 refYear/groups 外已过滤
}

// 类别主色与浅灰背景混合，柔化堆叠色
function softenHexColor(hex: string, ratio = 0.35): string {
  const normalized = hex.replace("#", "");
  if (normalized.length !== 6) return hex;
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);
  if ([r, g, b].some((v) => Number.isNaN(v))) return hex;
  const bg = 0xd9;
  const mix = (v: number) => Math.round(v * (1 - ratio) + bg * ratio);
  const toHex = (v: number) => v.toString(16).padStart(2, "0");
  return `#${toHex(mix(r))}${toHex(mix(g))}${toHex(mix(b))}`;
}

export default function TimelineChart({ projects }: TimelineChartProps) {
  const { t } = useLang();
  const [group, setGroup] = useState<VizGroup>(VIZ_GROUP_KEYS[0]);

  const data = useMemo(() => {
    // 选中大类下，按 年份 × 类别 计数（每项目每事件计一次）
    const map = new Map<number, Record<string, number>>();
    for (const p of projects) {
      // 同一项目在同一大类同一年只计一次（避免 中标+签约 同年同归"前期"被重复计数）
      const countedYears = new Set<number>();
      for (const e of p.stage_events ?? []) {
        if (e.group !== group) continue;
        if (countedYears.has(e.year)) continue;
        countedYears.add(e.year);
        const row = map.get(e.year) ?? {};
        row[p.category] = (row[p.category] ?? 0) + 1;
        map.set(e.year, row);
      }
    }
    return Array.from(map.entries())
      .sort(([a], [b]) => a - b)
      .map(([year, cats]) => ({ year, ...cats }));
  }, [projects, group]);

  const activeCats = Object.keys(CATEGORIES).filter((k) =>
    data.some((row) => (row as Record<string, unknown>)[k] != null),
  );

  const renderTooltip = ({
    active,
    payload,
    label,
  }: {
    active?: boolean;
    payload?: Array<{ name?: string; value?: number | string; color?: string }>;
    label?: string | number;
  }) => {
    if (!active || !payload || payload.length === 0) return null;
    const rows = payload.filter((item) => Number(item.value) > 0);
    const total = rows.reduce((sum, item) => sum + Number(item.value || 0), 0);
    return (
      <div className="bg-white border border-slate-200 rounded-lg px-2.5 py-2 shadow-sm text-[11px]">
        <div className="font-medium text-slate-700 mb-1">
          {t("chart.year")}: {label}
        </div>
        <div className="space-y-0.5">
          {rows.map((item, idx) => {
            const rawName = item.name || "";
            const cat = CATEGORIES[rawName];
            const labelText = cat ? cat.en : rawName;
            return (
              <div key={`${rawName}-${idx}`} className="flex items-center gap-1.5 text-slate-600">
                <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: item.color || "#94a3b8" }} />
                <span>{labelText}</span>
                <span className="ml-auto font-medium">{Number(item.value || 0)}</span>
              </div>
            );
          })}
        </div>
        <div className="mt-1 pt-1 border-t border-slate-100 flex items-center text-slate-700">
          <span>{t("chart.total")}</span>
          <span className="ml-auto font-semibold">{total}</span>
        </div>
      </div>
    );
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-xs font-medium text-slate-600">{t("chart.timelineTitle")}</div>
        <div className="flex gap-0.5">
          {VIZ_GROUP_KEYS.map((g) => (
            <button
              key={g}
              onClick={() => setGroup(g)}
              className={`px-1.5 py-0.5 rounded text-[10px] transition-colors ${
                group === g
                  ? "bg-brand-100 text-brand-700 font-medium"
                  : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {VIZ_GROUPS[g]?.en ?? g}
            </button>
          ))}
        </div>
      </div>
      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
          {t("chart.noData")}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="year" tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
            <Tooltip
              content={renderTooltip}
              wrapperStyle={{ zIndex: 9999, pointerEvents: "none" }}
              reverseDirection={{ x: true, y: false }}
              offset={14}
              allowEscapeViewBox={{ x: false, y: true }}
            />
            <Legend
              iconSize={8}
              iconType="circle"
              formatter={(value) => {
                const cat = CATEGORIES[value];
                return cat ? cat.en : value;
              }}
              wrapperStyle={{ fontSize: 10 }}
            />
            {activeCats.map((cat) => (
              <Bar
                key={cat}
                dataKey={cat}
                stackId="a"
                fill={softenHexColor(CATEGORIES[cat]?.color ?? "#94a3b8")}
                name={cat}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
