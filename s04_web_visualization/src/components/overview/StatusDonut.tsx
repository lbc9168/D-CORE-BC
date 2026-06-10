import { useMemo } from "react";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import type { Project, VizGroup } from "@/types";
import { VIZ_GROUPS, VIZ_GROUP_KEYS } from "@/types";
import { statusMix } from "@/utils/overviewStats";
import { useLang } from "@/contexts/LanguageContext";

/** 4 大类状态构成：左 donut + 右占比表 */
export default function StatusDonut({
  projects,
  year,
  title,
  shareLabel,
}: {
  projects: Project[];
  year: number | null;
  title: string;
  shareLabel: string;
}) {
  const { lang } = useLang();
  const rows = useMemo(() => {
    const mix = statusMix(projects, year);
    const total = Array.from(mix.values()).reduce((a, b) => a + b, 0);
    return VIZ_GROUP_KEYS.filter((k) => (mix.get(k) ?? 0) > 0).map((k: VizGroup) => ({
      key: k,
      label: lang === "zh" ? VIZ_GROUPS[k].zh : VIZ_GROUPS[k].en,
      color: VIZ_GROUPS[k].color,
      count: mix.get(k) ?? 0,
      pct: total > 0 ? ((mix.get(k) ?? 0) / total) * 100 : 0,
    }));
  }, [projects, year, lang]);

  return (
    <div className="card p-4 flex flex-col min-w-0">
      <h3 className="text-sm font-semibold text-slate-700 mb-2">{title}</h3>
      <div className="flex items-center gap-4 flex-1 min-h-[13rem]">
        <div className="w-40 h-40 flex-shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={rows} dataKey="count" nameKey="label" innerRadius="62%" outerRadius="95%"
                   paddingAngle={2} isAnimationActive={false}>
                {rows.map((r) => <Cell key={r.key} fill={r.color} stroke="none" />)}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
        <table className="flex-1 text-xs">
          <thead>
            <tr className="text-slate-400">
              <th className="text-left font-medium pb-1.5"> </th>
              <th className="text-right font-medium pb-1.5">{shareLabel}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-slate-50">
                <td className="py-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-full mr-2 align-middle"
                        style={{ backgroundColor: r.color }} />
                  <span className="text-slate-600 align-middle">{r.label}</span>
                </td>
                <td className="py-1.5 text-right font-semibold text-slate-700">
                  {r.pct.toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
