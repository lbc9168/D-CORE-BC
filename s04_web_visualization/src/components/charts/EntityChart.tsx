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
import { hasCapacity } from "@/utils/capacityPolicy";

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
  const { t } = useLang();
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
      // 储能/输变电建设单位非 MW，装机容量统计时不计入
      if (hasCapacity(p.category)) {
        existing.capacity += p.capacity_mw ?? 0;
      }
      map.set(p.primary_entity, existing);
    });

    return Array.from(map.values())
      .sort((a, b) => (mode === "count" ? b.count - a.count : b.capacity - a.capacity))
      .slice(0, TOP_N)
      .map((item) => ({
        name: item.name_en || item.name,
        count: item.count,
        capacity: Math.round(item.capacity),
      }));
  }, [projects, mode]);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        {t("chart.noData")}
      </div>
    );
  }

  // 颜色从品牌色深→浅（brand-teal 渐变）
  const colors = [
    "#0F4C48", "#1D6B67", "#2F6562", "#3D7D7A",
    "#56918E", "#70A8A6", "#8CBDBB", "#A8D0CF",
  ];

  return (
    <div className="h-full flex flex-col">
      <div className="text-sm font-semibold text-slate-700 mb-2">
        {t(mode === "count" ? "chart.entityTitleByCount" : "chart.entityTitleByCapacity")}
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
            reverseDirection={{ x: true, y: false }}
            offset={14}
            allowEscapeViewBox={{ x: false, y: true }}
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
