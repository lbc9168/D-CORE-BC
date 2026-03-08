import { useMemo } from "react";
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
import type { Project } from "@/types";
import { CATEGORIES } from "@/types";
import { useLang } from "@/contexts/LanguageContext";

interface TimelineChartProps {
  projects: Project[];
}

export default function TimelineChart({ projects }: TimelineChartProps) {
  const { t, lang } = useLang();

  const data = useMemo(() => {
    // 按年份 × 类别聚合
    const map = new Map<number, Record<string, number>>();
    projects.forEach((p) => {
      if (!p.event_year) return;
      const row = map.get(p.event_year) ?? {};
      row[p.category] = (row[p.category] ?? 0) + 1;
      map.set(p.event_year, row);
    });

    return Array.from(map.entries())
      .sort(([a], [b]) => a - b)
      .map(([year, cats]) => ({ year, ...cats }));
  }, [projects]);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        {t("chart.noData")}
      </div>
    );
  }

  const activeCats = Object.keys(CATEGORIES).filter((k) =>
    data.some((row) => (row as Record<string, unknown>)[k] != null),
  );

  return (
    <div className="h-full flex flex-col">
      <div className="text-xs font-medium text-slate-600 mb-2">{t("chart.timelineTitle")}</div>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis dataKey="year" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
          <Tooltip
            contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
          />
          <Legend
            iconSize={8}
            iconType="circle"
            formatter={(value) => {
              const cat = CATEGORIES[value];
              return cat ? (lang === "zh" ? cat.zh : cat.en) : value;
            }}
            wrapperStyle={{ fontSize: 10 }}
          />
          {activeCats.map((cat) => (
            <Bar
              key={cat}
              dataKey={cat}
              stackId="a"
              fill={CATEGORIES[cat]?.color ?? "#94a3b8"}
              name={cat}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
