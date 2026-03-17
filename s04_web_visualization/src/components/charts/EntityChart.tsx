import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import type { Project } from "@/types";
import { useLang } from "@/contexts/LanguageContext";

const TOP_N = 5;
const UNKNOWN_ENTITY_NAMES = new Set([
  "unknown",
  "未知",
  "未披露",
  "na",
  "n/a",
  "-",
  "—",
]);

function isUnknownEntity(name: string | null | undefined): boolean {
  if (!name) return true;
  const normalized = name.trim().toLowerCase();
  if (!normalized) return true;
  return UNKNOWN_ENTITY_NAMES.has(normalized);
}

interface EntityChartProps {
  projects: Project[];
}

type RankingMode = "count" | "capacity";

export default function EntityChart({ projects }: EntityChartProps) {
  const { t, lang } = useLang();
  const [mode, setMode] = useState<RankingMode>("count");

  const data = useMemo(() => {
    const map = new Map<string, { name: string; name_en: string; count: number; capacity: number }>();
    projects.forEach((p) => {
      if (isUnknownEntity(p.primary_entity)) return;
      const existing = map.get(p.primary_entity) ?? {
        name: p.primary_entity,
        name_en: p.primary_entity_en || p.primary_entity,
        count: 0,
        capacity: 0,
      };
      existing.count++;
      existing.capacity += p.capacity_mw ?? 0;
      map.set(p.primary_entity, existing);
    });

    return Array.from(map.values())
      .sort((a, b) => (mode === "count" ? b.count - a.count : b.capacity - a.capacity))
      .slice(0, TOP_N)
      .map((item) => ({
        name: (lang === "zh" ? item.name : item.name_en) || item.name || item.name_en,
        count: item.count,
        capacity: Math.round(item.capacity),
      }));
  }, [projects, lang, mode]);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        {t("chart.noData")}
      </div>
    );
  }

  // 颜色从品牌色深→浅
  const colors = [
    "#075985", "#0369a1", "#0284c7", "#0ea5e9", "#38bdf8",
    "#7dd3fc", "#bae6fd", "#0c4a6e", "#1e40af", "#1d4ed8",
    "#2563eb", "#3b82f6",
  ];

  return (
    <div className="h-full flex flex-col">
      <div className="text-xs font-medium text-slate-600 mb-2">
        {t("chart.entityTitle")}
      </div>
      <div className="mb-2 inline-flex rounded-lg border border-slate-200 bg-white p-0.5 w-fit">
        <button
          className={`px-2 py-1 text-[11px] rounded-md ${mode === "count" ? "bg-brand-600 text-white" : "text-slate-600"}`}
          onClick={() => setMode("count")}
        >
          {t("map.byCount")}
        </button>
        <button
          className={`px-2 py-1 text-[11px] rounded-md ${mode === "capacity" ? "bg-brand-600 text-white" : "text-slate-600"}`}
          onClick={() => setMode("capacity")}
        >
          {t("map.byCapacity")}
        </button>
      </div>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 24, left: 0, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 10 }} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fontSize: 10 }}
            width={120}
            tickFormatter={(v: string) => (v.length > 12 ? v.slice(0, 12) + "…" : v)}
          />
          <Tooltip
            contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
            wrapperStyle={{ zIndex: 9999, pointerEvents: "none" }}
            allowEscapeViewBox={{ x: true, y: true }}
            formatter={(v) =>
              mode === "count"
                ? [v, t("chart.projects")]
                : [`${Number(v).toLocaleString()} MW`, t("map.byCapacity")]
            }
          />
          <Bar dataKey={mode === "count" ? "count" : "capacity"} radius={[0, 4, 4, 0]}>
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
