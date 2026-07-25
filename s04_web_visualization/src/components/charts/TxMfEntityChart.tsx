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
import type { TxMfProject } from "@/types";
import { useLang } from "@/contexts/LanguageContext";
import { txmfTopEntities, filterTxMfKind } from "@/utils/txmfOverviewStats";
import KindToggle, { type TxMfKind } from "@/components/overview/KindToggle";

/** 产业链侧中方企业排名：视觉与发电侧 EntityChart 一致（品牌渐变横条+网格+胶囊切换），切换钮为 TX/MF 子域筛选 */
export default function TxMfEntityChart({ rows }: { rows: TxMfProject[] }) {
  const { t } = useLang();
  const [kind, setKind] = useState<TxMfKind>("all");

  const data = useMemo(
    () => txmfTopEntities(filterTxMfKind(rows, kind)).map((d) => ({ name: d.name, count: d.value })),
    [rows, kind],
  );

  // 颜色从品牌色深→浅（brand-teal 渐变，与 EntityChart 同一色板）
  const colors = [
    "#0F4C48", "#1D6B67", "#2F6562", "#3D7D7A",
    "#56918E", "#70A8A6", "#8CBDBB", "#A8D0CF",
  ];

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        {t("chart.noData")}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="text-sm font-semibold text-slate-700 mb-2">
        {t("chart.entityTitleByCount")}
      </div>
      <div className="mb-2">
        <KindToggle value={kind} onChange={setKind} />
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
            formatter={(v) => [v, t("map.projects")]}
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
