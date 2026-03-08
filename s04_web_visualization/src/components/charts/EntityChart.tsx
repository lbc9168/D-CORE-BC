import { useMemo } from "react";
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

const TOP_N = 12;

interface EntityChartProps {
  projects: Project[];
}

export default function EntityChart({ projects }: EntityChartProps) {
  const { t, lang } = useLang();

  const data = useMemo(() => {
    const map = new Map<string, { name: string; name_en: string; count: number }>();
    projects.forEach((p) => {
      if (!p.primary_entity) return;
      const existing = map.get(p.primary_entity) ?? {
        name: p.primary_entity,
        name_en: p.primary_entity_en || p.primary_entity,
        count: 0,
      };
      existing.count++;
      map.set(p.primary_entity, existing);
    });

    return Array.from(map.values())
      .sort((a, b) => b.count - a.count)
      .slice(0, TOP_N)
      .map((item) => ({
        name: lang === "zh" ? item.name : item.name_en,
        count: item.count,
      }));
  }, [projects, lang]);

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
            width={90}
            tickFormatter={(v: string) => (v.length > 9 ? v.slice(0, 9) + "…" : v)}
          />
          <Tooltip
            contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
            formatter={(v) => [v, t("chart.projects")]}
          />
          <Bar dataKey="count" radius={[0, 4, 4, 0]}>
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
