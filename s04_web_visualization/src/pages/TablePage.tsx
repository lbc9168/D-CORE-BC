import { useState, useMemo } from "react";
import { Search } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import type { Project } from "@/types";
import { CATEGORIES, STAGES, STAGE_COLORS } from "@/types";
import FilterBar from "@/components/filters/FilterBar";
import ProjectDetail from "@/components/projects/ProjectDetail";
import { formatCountryName } from "@/utils/display";

const PAGE_SIZE = 50;

export default function TablePage() {
  const { filteredProjects, loading, error } = useData();
  const { t, T } = useLang();

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

  // CSV 导出（英文列名 + UTF-8 BOM + remarks 备注列）
  const handleExport = () => {
    const exportHeaders: { key: string; label: string }[] = [
      { key: "project_id",          label: "Project ID" },
      { key: "project_name_en",     label: "Project Name (EN)" },
      { key: "country_iso",         label: "Country ISO" },
      { key: "province",            label: "Province / State" },
      { key: "category_en",         label: "Energy Type" },
      { key: "capacity_mw",         label: "Capacity (MW)" },
      { key: "latest_stage_en",     label: "Stage" },
      { key: "event_year",          label: "Year" },
      { key: "primary_entity_en",   label: "Primary Chinese Entity (EN)" },
      { key: "has_soe",             label: "Central SOE Involved" },
      { key: "bri_country",         label: "BRI Country" },
      { key: "ldc",                 label: "LDC" },
      { key: "financing_model_en",  label: "Financing Model" },
      { key: "remarks",             label: "Remarks" },
    ];

    /** 单元格安全转义：含逗号/引号/换行时加双引号包裹 */
    const esc = (v: unknown): string => {
      if (v == null) return "";
      const s = String(v);
      return s.includes(",") || s.includes('"') || s.includes("\n")
        ? '"' + s.replace(/"/g, '""') + '"'
        : s;
    };

    const rows = sorted.map((p) => {
      // 构建 remarks：其他中企 + 融资方 + 多阶段时间线
      const others = (p.all_entities ?? [])
        .filter((e) => e.name !== p.primary_entity && (e.name || e.name_en))
        .map((e) => (e.name_en ? `${e.name_en} (${e.name})` : e.name))
        .join("; ");
      const financing = (p.all_entities ?? [])
        .filter((e) => e.involvement_type_en?.includes("Financ") || e.involvement_type?.includes("融资"))
        .map((e) => e.name_en || e.name)
        .join("; ");
      const parts: string[] = [];
      if (others)           parts.push(`Other entities: ${others}`);
      if (financing)        parts.push(`Financing: ${financing}`);
      if (p.timeline_notes) parts.push(`Timeline: ${p.timeline_notes}`);
      const remarks = parts.join(" | ");

      return exportHeaders.map(({ key }) => {
        if (key === "remarks")  return esc(remarks);
        if (key === "has_soe")  return esc(p.has_soe === 1 ? "Yes" : "No");
        return esc((p as Record<string, unknown>)[key]);
      }).join(",");
    });

    // UTF-8 BOM 解决 Excel 直接打开乱码
    const BOM = "\uFEFF";
    const csv = BOM + exportHeaders.map((h) => h.label).join(",") + "\n" + rows.join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
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
      <div className="flex flex-1 items-center justify-center text-slate-400 text-sm">
        {t("common.loading")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center text-red-400 text-sm">
        {t("common.error")}
      </div>
    );
  }

  const col = T.table.columns;

  return (
    <div className="flex flex-1 overflow-hidden">
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
          {/* <button onClick={handleExport} className="btn-outline text-xs">
            <Download size={13} />
            {t("table.export")}
          </button> */}
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
                  {col.name || "Project Name"}
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="country" label={col.country || "Country"} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  {col.province || "Province"}
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  {col.category || "Category"}
                </th>
                <th className="text-right px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="capacity_mw" label={col.capacity || "Capacity (MW)"} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="latest_stage" label={col.stage || "Stage"} />
                </th>
                <th className="text-right px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="event_year" label={col.year || "Year"} />
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 min-w-32">
                  {col.entity || "Chinese Entity"}
                </th>
                <th className="text-center px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  Central SOE
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {pageData.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-12 text-center text-slate-400">
                    {t("table.empty")}
                  </td>
                </tr>
              ) : (
                pageData.map((p) => {
                  const cat = CATEGORIES[p.category];
                  const stageColor = STAGE_COLORS[p.latest_stage] ?? "#94a3b8";
                  const name = p.project_name_en || p.project_name;
                  const entity = p.primary_entity_en || p.primary_entity;
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
                      <td className="px-3 py-2 text-slate-600 whitespace-nowrap">
                        {formatCountryName(p.country, p.country_iso, "en")}
                      </td>
                      <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                        {p.province ?? "—"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {cat ? (
                          <span
                            className="badge text-white"
                            style={{ backgroundColor: cat.color, fontSize: "10px" }}
                          >
                            {cat.en}
                          </span>
                        ) : (p.category_en || p.category) ? (
                          <span
                            className="badge bg-slate-100 text-slate-500"
                            style={{ fontSize: "10px" }}
                          >
                            {p.category_en || p.category}
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
                          {p.latest_stage_en || STAGES[p.latest_stage]?.en || p.latest_stage}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600">{p.event_year}</td>
                      <td className="px-3 py-2 text-slate-600 max-w-xs truncate">{entity}</td>
                      <td className="px-3 py-2 text-center">
                        {p.has_soe === 1 ? (
                          <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold
                                           bg-amber-100 text-amber-700 leading-none">
                            ✓
                          </span>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
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
        <div className="w-80 border-l border-slate-200 flex-shrink-0 overflow-hidden bg-white shadow-lg">
          <ProjectDetail
            project={selectedProject}
            onClose={() => setSelectedProject(null)}
          />
        </div>
      )}
    </div>
  );
}
