import { useState, useMemo } from "react";
import { Search, Download } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import type { Project } from "@/types";
import { CATEGORIES, STAGES, STAGE_COLORS } from "@/types";
import FilterBar from "@/components/filters/FilterBar";
import ProjectDetail from "@/components/projects/ProjectDetail";

const PAGE_SIZE = 50;

export default function TablePage() {
  const { filteredProjects, loading, error } = useData();
  const { t, lang } = useLang();

  const [colorBy, setColorBy] = useState<"count" | "capacity">("count");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<keyof Project>("event_year");
  const [sortAsc, setSortAsc] = useState(false);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

  // 搜索
  const searched = useMemo(() => {
    if (!search.trim()) return filteredProjects;
    const q = search.toLowerCase();
    return filteredProjects.filter(
      (p) =>
        p.project_name?.toLowerCase().includes(q) ||
        p.project_name_en?.toLowerCase().includes(q) ||
        p.primary_entity?.toLowerCase().includes(q) ||
        p.primary_entity_en?.toLowerCase().includes(q) ||
        p.country?.toLowerCase().includes(q),
    );
  }, [filteredProjects, search]);

  // 排序
  const sorted = useMemo(() => {
    return [...searched].sort((a, b) => {
      const av = a[sortKey] as string | number | null;
      const bv = b[sortKey] as string | number | null;
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortAsc ? cmp : -cmp;
    });
  }, [searched, sortKey, sortAsc]);

  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
  const pageData = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const handleSort = (key: keyof Project) => {
    if (key === sortKey) setSortAsc((v) => !v);
    else { setSortKey(key); setSortAsc(false); }
    setPage(0);
  };

  // CSV 导出
  const handleExport = () => {
    const headers = ["project_id", "project_name", "project_name_en", "country",
      "province", "category", "capacity_mw", "latest_stage", "event_year",
      "primary_entity", "primary_entity_en", "verification_status"];
    const rows = sorted.map((p) =>
      headers.map((h) => {
        const v = (p as Record<string, unknown>)[h];
        const s = v == null ? "" : String(v);
        return s.includes(",") ? `"${s}"` : s;
      }).join(",")
    );
    const blob = new Blob([headers.join(",") + "\n" + rows.join("\n")], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "bri_re_projects.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const SortBtn = ({ k, label }: { k: keyof Project; label: string }) => (
    <button
      onClick={() => handleSort(k)}
      className="flex items-center gap-0.5 hover:text-brand-600 transition-colors"
    >
      {label}
      <span className="text-slate-300">
        {sortKey === k ? (sortAsc ? " ▲" : " ▼") : " ⬍"}
      </span>
    </button>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        {t("common.loading")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-400 text-sm">
        {t("common.error")}
      </div>
    );
  }

  const col = t("table.columns") as Record<string, string>;

  return (
    <div className="flex h-full overflow-hidden">
      {/* 主表格区域 */}
      <div className="flex flex-col flex-1 overflow-hidden">
        <FilterBar colorBy={colorBy} onColorByChange={setColorBy} />

        {/* 工具栏 */}
        <div className="flex items-center gap-3 px-4 py-2 bg-white border-b border-slate-100 flex-shrink-0">
          <div className="relative flex-1 max-w-xs">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(0); }}
              placeholder={t("table.search")}
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-slate-200 rounded-lg
                         focus:outline-none focus:ring-1 focus:ring-brand-400"
            />
          </div>
          <span className="text-xs text-slate-400 ml-auto">
            {t("table.total", { count: sorted.length })}
          </span>
          <button onClick={handleExport} className="btn-outline text-xs">
            <Download size={13} />
            {t("table.export")}
          </button>
        </div>

        {/* 表格 */}
        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-slate-50 z-10">
              <tr className="border-b border-slate-200">
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="project_id" label={col["id"] ?? "ID"} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 min-w-48">
                  {col["name"]}
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="country" label={col["country"] ?? ""} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  {col["province"]}
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  {col["category"]}
                </th>
                <th className="text-right px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="capacity_mw" label={col["capacity"] ?? ""} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="latest_stage" label={col["stage"] ?? ""} />
                </th>
                <th className="text-right px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="event_year" label={col["year"] ?? ""} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 min-w-32">
                  {col["entity"]}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {pageData.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-12 text-center text-slate-400">
                    {t("table.empty")}
                  </td>
                </tr>
              ) : (
                pageData.map((p) => {
                  const cat = CATEGORIES[p.category];
                  const stageColor = STAGE_COLORS[p.latest_stage] ?? "#94a3b8";
                  const name = lang === "zh" ? p.project_name : (p.project_name_en || p.project_name);
                  const entity = lang === "zh" ? p.primary_entity : (p.primary_entity_en || p.primary_entity);
                  const isSelected = selectedProject?.project_id === p.project_id;

                  return (
                    <tr
                      key={p.project_id}
                      className={`hover:bg-slate-50 cursor-pointer transition-colors ${
                        isSelected ? "bg-brand-50" : ""
                      }`}
                      onClick={() => setSelectedProject(p)}
                    >
                      <td className="px-3 py-2 text-slate-400 font-mono whitespace-nowrap">
                        {p.project_id}
                      </td>
                      <td className="px-3 py-2 text-slate-800 line-clamp-2 max-w-xs">
                        {name}
                      </td>
                      <td className="px-3 py-2 text-slate-600 whitespace-nowrap">{p.country}</td>
                      <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                        {p.province ?? "—"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {cat ? (
                          <span
                            className="badge text-white"
                            style={{ backgroundColor: cat.color, fontSize: "10px" }}
                          >
                            {lang === "zh" ? cat.zh : cat.en}
                          </span>
                        ) : p.category ? (
                          <span
                            className="badge bg-slate-100 text-slate-500"
                            style={{ fontSize: "10px" }}
                          >
                            {p.category}
                          </span>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600 whitespace-nowrap">
                        {p.capacity_mw?.toFixed(0) ?? "—"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <span
                          className="badge"
                          style={{
                            backgroundColor: stageColor + "20",
                            color: stageColor,
                            fontSize: "10px",
                          }}
                        >
                          {lang === "zh"
                            ? p.latest_stage
                            : (STAGES[p.latest_stage]?.en ?? p.latest_stage)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600">{p.event_year}</td>
                      <td className="px-3 py-2 text-slate-600 max-w-xs truncate">{entity}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* 分页 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 py-2 border-t border-slate-100
                          bg-white flex-shrink-0 text-xs text-slate-600">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="btn-outline disabled:opacity-40 py-1 px-2"
            >
              ←
            </button>
            <span>
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
              className="btn-outline disabled:opacity-40 py-1 px-2"
            >
              →
            </button>
          </div>
        )}
      </div>

      {/* 右：项目详情侧边栏 */}
      {selectedProject && (
        <div className="w-80 border-l border-slate-200 flex-shrink-0 overflow-hidden">
          <ProjectDetail
            project={selectedProject}
            onClose={() => setSelectedProject(null)}
          />
        </div>
      )}
    </div>
  );
}
