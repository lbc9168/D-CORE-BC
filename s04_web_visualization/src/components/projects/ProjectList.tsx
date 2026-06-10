import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import type { Project } from "@/types";
import { CATEGORIES, VIZ_GROUPS } from "@/types";
import { Zap } from "lucide-react";

interface ProjectListProps {
  projects: Project[];
  onSelect: (project: Project) => void;
  selectedId: string | null;
}

export default function ProjectList({ projects, onSelect, selectedId }: ProjectListProps) {
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
                {p.capacity_mw != null && (
                  <span className="text-slate-400" style={{ fontSize: "10px" }}>
                    {p.capacity_mw.toFixed(0)} MW
                  </span>
                )}
                {p.event_year && (
                  <span className="text-slate-400 ml-auto" style={{ fontSize: "10px" }}>
                    {p.event_year}
                  </span>
                )}
              </div>

              {/* 中方实体 */}
              {p.primary_entity && (
                <div className="text-slate-500 mt-0.5" style={{ fontSize: "11px" }}>
                  {p.primary_entity_en || p.primary_entity}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
