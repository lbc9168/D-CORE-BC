import { useMemo } from "react";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell,
} from "recharts";
import type { Project } from "@/types";
import { topCountries, formatBig } from "@/utils/overviewStats";
import { formatCountryName } from "@/utils/display";

/** Top N 国家横向条形图（metric=count|capacity） */
export default function TopCountriesBar({
  projects,
  metric,
  title,
  color,
}: {
  projects: Project[];
  metric: "count" | "capacity";
  title: string;
  color: string;
}) {
  const data = useMemo(
    () =>
      topCountries(projects, metric).map((d) => ({
        name: formatCountryName(d.iso, d.iso, "en"),
        value: Math.round(d.value * 10) / 10,
      })),
    [projects, metric],
  );

  return (
    <div className="card p-4 flex flex-col min-w-0">
      <h3 className="text-sm font-semibold text-slate-700 mb-2">{title}</h3>
      <div className="flex-1 min-h-[18rem]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 36, bottom: 0, left: 8 }}>
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="name" width={92}
                   tick={{ fontSize: 10, fill: "#64748b" }} axisLine={false} tickLine={false} />
            <Tooltip
              formatter={(v) => formatBig(Number(v))}
              cursor={{ fill: "#f1f5f9" }}
            />
            <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={12}
                 label={{ position: "right", fontSize: 10, fill: "#64748b", formatter: (v: number) => formatBig(v) }}
                 isAnimationActive={false}>
              {data.map((_, i) => <Cell key={i} fill={color} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
