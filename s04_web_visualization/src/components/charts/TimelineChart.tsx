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
  Cell,
} from "recharts";
import type { Project, VizGroup } from "@/types";
import { CATEGORIES } from "@/types";
import { useLang } from "@/contexts/LanguageContext";
import { BASELINE_YEAR } from "@/contexts/DataContext";
import { hasCapacity } from "@/utils/capacityPolicy";

type Metric = "count" | "capacity";

// 里程碑趋势/构成图：All Years=年×类别堆叠趋势；具体年=该年各能源类别分布。指标可切 项目数/装机容量。
// 数据口径：活动模型——项目在其有里程碑的每个年份各计一次；类别用原色（与站内徽章一致）。
interface TimelineChartProps {
  projects: Project[];   // OverviewPage 传 allProjects
  year: number | null;   // null = 全部年份（趋势）；具体年 = 该年类别分布
  groups: VizGroup[];    // 里程碑大类过滤；空数组 = 全部大类
}

interface CatDatum {
  key: string;
  label: string;
  value: number;
  color: string;
}

// 数值格式化：容量带 MW，项目数取整
function fmtValue(v: number, metric: Metric): string {
  return metric === "capacity" ? `${Math.round(v).toLocaleString()} MW` : String(Math.round(v));
}

export default function TimelineChart({ projects, year, groups }: TimelineChartProps) {
  const { t } = useLang();
  const [metric, setMetric] = useState<Metric>("count");

  const { trendData, trendCats, catData } = useMemo(() => {
    const allowed = (g: VizGroup) => groups.length === 0 || groups.includes(g);
    const inc = (p: Project) => (metric === "capacity" ? p.capacity_mw ?? 0 : 1);
    // 容量模式跳过无 MW 装机的类别（储能/输变电/制造）
    const capOk = (p: Project) => metric !== "capacity" || hasCapacity(p.category);

    if (year == null) {
      // ── All Years：年 × 类别（活动模型，项目在每个有里程碑的年份各计一次）──
      const map = new Map<number, Record<string, number>>();
      for (const p of projects) {
        if (!capOk(p)) continue;
        const add = inc(p);
        if (add === 0) continue;
        const activeYears = new Set<number>();
        for (const e of p.stage_events ?? []) {
          if (e.year < BASELINE_YEAR || !allowed(e.group)) continue;
          activeYears.add(e.year);
        }
        for (const y of activeYears) {
          const row = map.get(y) ?? {};
          row[p.category] = (row[p.category] ?? 0) + add;
          map.set(y, row);
        }
      }
      const trendData = Array.from(map.entries())
        .sort(([a], [b]) => a - b)
        .map(([yr, cats]) => ({ year: yr, ...cats }));
      const trendCats = Object.keys(CATEGORIES).filter((k) =>
        trendData.some((r) => (r as Record<string, unknown>)[k] != null),
      );
      return { trendData, trendCats, catData: [] as CatDatum[] };
    }

    // ── 具体年：该年有里程碑的项目，按能源类别聚合 ──
    const acc = new Map<string, number>();
    for (const p of projects) {
      if (!capOk(p)) continue;
      const active = (p.stage_events ?? []).some((e) => e.year === year && allowed(e.group));
      if (!active) continue;
      const add = inc(p);
      if (add === 0) continue;
      acc.set(p.category, (acc.get(p.category) ?? 0) + add);
    }
    const catData: CatDatum[] = Object.keys(CATEGORIES)
      .filter((k) => (acc.get(k) ?? 0) > 0)
      .map((k) => ({ key: k, label: CATEGORIES[k].en, value: acc.get(k) as number, color: CATEGORIES[k].color }));
    return { trendData: [] as Record<string, number>[], trendCats: [] as string[], catData };
  }, [projects, year, groups, metric]);

  const isYear = year != null;
  const title = isYear
    ? `${year} ${metric === "count" ? t("chart.byTypeCount") : t("chart.byTypeCapacity")}`
    : metric === "count" ? t("chart.trendCount") : t("chart.trendCapacity");
  const hasData = isYear ? catData.length > 0 : trendData.length > 0;

  // 堆叠趋势 tooltip（年 + 各类别 + 合计）
  const trendTooltip = ({
    active, payload, label,
  }: {
    active?: boolean;
    payload?: Array<{ name?: string; value?: number | string; color?: string }>;
    label?: string | number;
  }) => {
    if (!active || !payload || payload.length === 0) return null;
    const rows = payload.filter((item) => Number(item.value) > 0);
    const total = rows.reduce((sum, item) => sum + Number(item.value || 0), 0);
    return (
      <div
        className="bg-white border border-slate-200 rounded-lg px-2.5 py-2 shadow-sm text-[11px]"
        style={{ transform: "translateY(-50%)" }}
      >
        <div className="font-medium text-slate-700 mb-1">{t("chart.year")}: {label}</div>
        <div className="space-y-0.5">
          {rows.map((item, idx) => (
            <div key={`${item.name}-${idx}`} className="flex items-center gap-1.5 text-slate-600">
              <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: item.color || "#94a3b8" }} />
              <span>{CATEGORIES[item.name ?? ""]?.en ?? item.name}</span>
              <span className="ml-auto font-medium">{fmtValue(Number(item.value || 0), metric)}</span>
            </div>
          ))}
        </div>
        <div className="mt-1 pt-1 border-t border-slate-100 flex items-center text-slate-700">
          <span>{t("chart.total")}</span>
          <span className="ml-auto font-semibold">{fmtValue(total, metric)}</span>
        </div>
      </div>
    );
  };

  // 具体年类别 tooltip（单类别 + 数值）
  const yearTooltip = ({
    active, payload,
  }: {
    active?: boolean;
    payload?: Array<{ payload?: CatDatum }>;
  }) => {
    if (!active || !payload || payload.length === 0) return null;
    const d = payload[0]?.payload;
    if (!d) return null;
    return (
      <div
        className="bg-white border border-slate-200 rounded-lg px-2.5 py-2 shadow-sm text-[11px]"
        style={{ transform: "translateY(-50%)" }}
      >
        <div className="flex items-center gap-1.5 text-slate-700">
          <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: d.color }} />
          <span>{d.label}</span>
          <span className="ml-auto font-semibold">{fmtValue(Number(d.value || 0), metric)}</span>
        </div>
      </div>
    );
  };

  return (
    <div className="h-full flex flex-col">
      <div className="text-sm font-semibold text-slate-700 mb-2">{title}</div>

      {/* 指标切换（同 EntityChart） */}
      <div className="mb-2 inline-flex rounded-lg border border-slate-200 bg-white p-0.5 w-fit">
        <button
          className={`px-2 py-1 text-[11px] rounded-md ${metric === "count" ? "bg-brand-600 text-white" : "text-slate-600"}`}
          onClick={() => setMetric("count")}
        >
          {t("map.byCount")}
        </button>
        <button
          className={`px-2 py-1 text-[11px] rounded-md ${metric === "capacity" ? "bg-brand-600 text-white" : "text-slate-600"}`}
          onClick={() => setMetric("capacity")}
        >
          {t("map.byCapacity")}
        </button>
      </div>

      {!hasData ? (
        <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
          {t("chart.noData")}
        </div>
      ) : isYear ? (
        // 具体年：每个能源类别一根柱（原色）
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={catData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10 }}
              interval={0}
              tickFormatter={(v: string) => (v.length > 10 ? v.slice(0, 10) + "…" : v)}
            />
            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
            <Tooltip
              content={yearTooltip}
              wrapperStyle={{ zIndex: 9999, pointerEvents: "none" }}
              offset={14}
              allowEscapeViewBox={{ x: false, y: true }}
            />
            <Bar dataKey="value" radius={[3, 3, 0, 0]}>
              {catData.map((d) => (
                <Cell key={d.key} fill={d.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      ) : (
        // All Years：年 × 类别堆叠（原色）
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={trendData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="year" tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
            <Tooltip
              content={trendTooltip}
              wrapperStyle={{ zIndex: 9999, pointerEvents: "none" }}
              reverseDirection={{ x: true, y: false }}
              offset={14}
              allowEscapeViewBox={{ x: false, y: true }}
            />
            <Legend
              iconSize={8}
              iconType="circle"
              formatter={(value) => CATEGORIES[value]?.en ?? value}
              wrapperStyle={{ fontSize: 10 }}
            />
            {trendCats.map((cat) => (
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
      )}
    </div>
  );
}
