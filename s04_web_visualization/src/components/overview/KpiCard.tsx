import type { LucideIcon } from "lucide-react";

/** KPI 大数字卡（cobalt 风格）：突出大数字 + 右上角圆形图标 */
export default function KpiCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  color: string; // 图标主色，如 "#2F6562"
}) {
  return (
    <div className="card p-5 flex flex-col gap-2 min-w-0">
      <div className="flex items-start justify-between gap-2">
        <div className="text-3xl font-bold text-slate-800 leading-tight">{value}</div>
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ backgroundColor: color }}
        >
          <Icon size={17} className="text-white" />
        </div>
      </div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
    </div>
  );
}
