import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import type { Project } from "@/types";
import { CATEGORIES, VIZ_GROUPS } from "@/types";
import { Zap } from "lucide-react";
import TechnologyTagBadges from "./TechnologyTagBadges";

interface ProjectListProps {
  projects: Project[];
  onSelect: (project: Project) => void;
  selectedId: string | null;
  /** storage = 储能域视图：规模标签以 MWh/MW 储能口径优先（默认发电口径 MW） */
  variant?: "default" | "storage";
}

/**
 * 规模标签：默认口径 capacity MW（ES 项目 capacity 恒空时回退储能双指标）；
 * storage 域口径 MWh 优先，光储项目附 PV 子项装机。
 */
function scaleLabel(p: Project, variant: "default" | "storage"): string | null {
  const storageParts: string[] = [];
  if (p.storage_power_mw != null) storageParts.push(`${p.storage_power_mw.toFixed(0)} MW`);
  if (p.storage_energy_mwh != null) storageParts.push(`${p.storage_energy_mwh.toFixed(0)} MWh`);

  if (variant === "storage") {
    const parts: string[] = [];
    if (p.storage_energy_mwh != null) parts.push(`${p.storage_energy_mwh.toFixed(0)} MWh`);
    else if (p.storage_power_mw != null) parts.push(`${p.storage_power_mw.toFixed(0)} MW`);
    if (p.pv_capacity_mw != null) parts.push(`PV ${p.pv_capacity_mw.toFixed(0)} MW`);
    if (parts.length) return parts.join(" · ");
    return p.capacity_mw != null ? `${p.capacity_mw.toFixed(0)} MW` : null;
  }

  if (p.capacity_mw != null) return `${p.capacity_mw.toFixed(0)} MW`;
  return storageParts.length ? storageParts.join(" · ") : null;
}

export default function ProjectList({ projects, onSelect, selectedId, variant = "default" }: ProjectListProps) {
  const { t } = useLang();
  const { statusGroupOf } = useData();

  if (projects.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2 py-8">
        <Zap size={24} strokeWidth={1} />
        <span className="text-sm">{t("projectList.noProjects")}</span>
      </div>
    );
  }

  // 默认按 display_order 升序（老项目在前）；缺失值排末尾
  const ordered = [...projects].sort(
    (a, b) =>
      (a.display_order ?? Number.MAX_SAFE_INTEGER) -
      (b.display_order ?? Number.MAX_SAFE_INTEGER)
  );

  return (
    <div className="overflow-y-auto h-full">
      <ul className="divide-y divide-slate-50">
        {ordered.map((p) => {
          const cat = CATEGORIES[p.category];
          const grp = statusGroupOf(p);
          const grpMeta = grp ? VIZ_GROUPS[grp] : null;
          const stageColor = grpMeta?.color ?? "#94a3b8";
          const isSelected = p.project_id === selectedId;
          const name = p.project_name_en || p.project_name;

          return (
            <li
              key={p.project_id}
              className={`px-3 py-2.5 cursor-pointer transition-colors hover:bg-slate-50 ${
                isSelected ? "bg-brand-50 border-l-2 border-brand-500" : ""
              }`}
              onClick={() => onSelect(p)}
            >
              {/* 项目名 */}
              <div className="text-sm font-medium text-slate-800 leading-tight line-clamp-2 mb-1">
                {name}
              </div>

              {/* 标签行 */}
              <div className="flex flex-wrap items-center gap-1">
                {cat && (
                  <span
                    className="badge text-white"
                    style={{ backgroundColor: cat.color, fontSize: "10px" }}
                  >
                    {cat.en}
                  </span>
                )}
                {p.subcategory_en && (
                  <span
                    className="badge border bg-white"
                    style={{
                      color: cat?.color ?? "#64748B",
                      borderColor: (cat?.color ?? "#64748B") + "66",
                      fontSize: "10px",
                    }}
                  >
                    {p.subcategory_en}
                  </span>
                )}
                <TechnologyTagBadges project={p} compact />
                <span
                  className="badge"
                  style={{
                    backgroundColor: stageColor + "20",
                    color: stageColor,
                    fontSize: "10px",
                  }}
                >
                  {grpMeta?.en ?? "—"}
                </span>
                {scaleLabel(p, variant) && (
                  <span className="text-slate-400" style={{ fontSize: "10px" }}>
                    {scaleLabel(p, variant)}
                  </span>
                )}
                {p.event_year && (
                  <span className="text-slate-400 ml-auto" style={{ fontSize: "10px" }}>
                    {p.event_year}
                  </span>
                )}
              </div>

              {/* 业主/开发商（无值或 unknown 显示 —）*/}
              {(() => {
                const dev = (p.project_developer_en || "").trim();
                const isUnknown =
                  !dev || ["unknown", "未知", "未披露", "n/a", "na", "-", "—"].includes(dev.toLowerCase());
                return (
                  <div className="text-slate-500 mt-0.5" style={{ fontSize: "11px" }}>
                    {isUnknown ? "—" : dev}
                  </div>
                );
              })()}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
