import { useMemo, type ReactNode } from "react";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell,
} from "recharts";
import { topCountries, formatBig, type CountryRow } from "@/utils/overviewStats";
import { formatCountryName } from "@/utils/display";

/** Top N 国家横向条形图（metric=count|capacity；行结构最小化，主表/专表通用） */
export default function TopCountriesBar({
  projects,
  metric,
  title,
  color,
  headerExtra,
}: {
  projects: CountryRow[];
  metric: "count" | "capacity";
  title: string;
  color: string;
  headerExtra?: ReactNode;
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
      <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
        <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
        {headerExtra}
      </div>
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
