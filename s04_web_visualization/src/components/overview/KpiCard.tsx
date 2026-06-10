import type { LucideIcon } from "lucide-react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";

/** KPI 大数字卡（cobalt 风格）：大数字 + 右上角圆形图标 + 底部迷你趋势线 */
export default function KpiCard({
  label,
  value,
  icon: Icon,
  color,
  series,
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  color: string; // 趋势线与图标主色，如 "#2F6562"
  series: { year: number; v: number }[];
}) {
  // SVG 渐变 id 不能含中文/空格，用色值派生（四卡颜色互异，无冲突）
  const gradId = `kpi-${color.replace("#", "")}`;
  return (
    <div className="card p-4 flex flex-col gap-1 min-h-[10rem]">
      <div className="flex items-start justify-between">
        <div className="text-2xl font-bold text-slate-800 leading-tight">{value}</div>
        <div
          className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ backgroundColor: color }}
        >
          <Icon size={15} className="text-white" />
        </div>
      </div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="flex-1 min-h-[3.5rem] mt-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.25} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="v"
              stroke={color}
              strokeWidth={2}
              fill={`url(#${gradId})`}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
