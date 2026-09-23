import { useLang } from "@/contexts/LanguageContext";
import type { TxMfProject } from "@/types";
import { CATEGORIES, VIZ_GROUPS } from "@/types";
import { stageToGroup } from "@/utils/enums";
import { TX_CATEGORY_KEY as TX_KEY, MF_CATEGORY_KEY as MF_KEY } from "@/utils/domains";
import { Zap } from "lucide-react";
import { formatProductionCapacity } from "@/utils/display";

interface TxMfProjectListProps {
  projects: TxMfProject[];
  onSelect: (project: TxMfProject) => void;
  selectedId: string | null;
}

/** 规模标签：TX = 电压/里程/输送 有值部分拼接；MF = 产能值+单位；全空返回 null（TablePage Specs 列复用） */
export function txmfScaleLabel(p: TxMfProject): string | null {
  if (p.kind === "tx") {
    const parts: string[] = [];
    if (p.voltage_kv != null) parts.push(`${p.voltage_kv} kV`);
    if (p.line_length_km != null) parts.push(`${p.line_length_km} km`);
    if (p.transmission_capacity_mw != null) parts.push(`${p.transmission_capacity_mw} MW`);
    return parts.length ? parts.join(" · ") : null;
  }
  if (p.production_capacity_value != null) {
    return formatProductionCapacity(p.production_capacity_value, p.production_capacity_unit);
  }
  return null;
}

/** TX/MF 专表项目列表：视觉结构与 ProjectList 一致，字段换成专表口径（无时间线状态快照） */
export default function TxMfProjectList({ projects, onSelect, selectedId }: TxMfProjectListProps) {
  const { t } = useLang();

  if (projects.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2 py-8">
        <Zap size={24} strokeWidth={1} />
        <span className="text-sm">{t("projectList.noProjects")}</span>
      </div>
    );
  }

  // 专表无 display_order，按最新事件年降序；同年按 project_id 稳定排序，缺失年份排末尾
  const ordered = [...projects].sort((a, b) => {
    if (a.event_year == null && b.event_year == null) {
      return a.project_id.localeCompare(b.project_id);
    }
    if (a.event_year == null) return 1;
    if (b.event_year == null) return -1;
    return b.event_year - a.event_year || a.project_id.localeCompare(b.project_id);
  });

  return (
    <div className="overflow-y-auto h-full">
      <ul className="divide-y divide-slate-50">
        {ordered.map((p) => {
          const cat = CATEGORIES[p.kind === "tx" ? TX_KEY : MF_KEY];
          const grp = stageToGroup(p.latest_stage);
          const grpMeta = grp ? VIZ_GROUPS[grp] : null;
          const stageColor = grpMeta?.color ?? "#94a3b8";
          const isSelected = p.project_id === selectedId;
          const name = p.project_name_en || p.project_name;
          const scale = txmfScaleLabel(p);
          // 业主字段专表没有：显示第一家参与实体（无则 —）
          const firstEntity = p.all_entities?.[0];
          const entityLabel = firstEntity ? firstEntity.name_en || firstEntity.name : "—";

          return (
            <li
              key={p.project_id}
              className={`px-3 py-2.5 cursor-pointer transition-colors hover:bg-slate-50 ${
                isSelected ? "bg-brand-50 border-l-2 border-brand-500" : ""
              }`}
              onClick={() => onSelect(p)}
            >
              <div className="text-sm font-medium text-slate-800 leading-tight line-clamp-2 mb-1">
                {name}
              </div>

              <div className="flex flex-wrap items-center gap-1">
                {cat && (
                  <span
                    className="badge text-white"
                    style={{ backgroundColor: cat.color, fontSize: "10px" }}
                  >
                    {cat.en}
                  </span>
                )}
                <span
                  className="badge"
                  style={{ backgroundColor: stageColor + "20", color: stageColor, fontSize: "10px" }}
                >
                  {grpMeta?.en ?? "—"}
                </span>
                {scale && (
                  <span className="text-slate-400" style={{ fontSize: "10px" }}>
                    {scale}
                  </span>
                )}
                {p.event_year && (
                  <span className="text-slate-400 ml-auto" style={{ fontSize: "10px" }}>
                    {p.event_year}
                  </span>
                )}
              </div>

              <div className="text-slate-500 mt-0.5" style={{ fontSize: "11px" }}>
                {entityLabel}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
