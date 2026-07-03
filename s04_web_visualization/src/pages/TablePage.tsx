import { useState, useMemo } from "react";
import { Search, Download, FileSpreadsheet, AlertCircle, X } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import type { Project } from "@/types";
import { CATEGORIES, STAGES, STAGE_COLORS } from "@/types";
import FilterBar from "@/components/filters/FilterBar";
import ProjectDetailDrawer from "@/components/projects/ProjectDetailDrawer";
import { formatCountryName } from "@/utils/display";
import { downloadCSV } from "@/utils/csvExport";
import DownloadSurveyModal, { saveSurveyRecord, type SurveyData } from "@/components/DownloadSurveyModal";

const SURVEY_ENDPOINT = import.meta.env.VITE_SURVEY_ENDPOINT as string | undefined;

const PAGE_SIZE = 50;

export default function TablePage() {
  const { filteredProjects, loading, error } = useData();
  const { t, T } = useLang();

  const [colorBy, setColorBy] = useState<"count" | "capacity">("count");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<keyof Project>("display_order");
  const [sortAsc, setSortAsc] = useState(true);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [showSurvey, setShowSurvey] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);

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
        p.project_developer?.toLowerCase().includes(q) ||
        p.project_developer_en?.toLowerCase().includes(q) ||
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

  // 获取静态 events_export.csv 并触发浏览器下载；失败时弹出可见提示
  const triggerDownload = async (data?: SurveyData) => {
    const base = import.meta.env.BASE_URL;
    const filename = `dcore_events_${new Date().toISOString().slice(0, 10)}.csv`;
    try {
      const res = await fetch(`${base}data/events_export.csv`);
      if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
      // fetch().text() 会剥掉源文件的 UTF-8 BOM；重新补 BOM 以免 Excel 按 GBK 打开导致中文乱码
      const text = await res.text();
      downloadCSV("﻿" + text.replace(/^﻿/, ""), filename);
    } catch (err) {
      console.error("下载 events_export.csv 失败", err);
      setDownloadFailed(true);
      window.setTimeout(() => setDownloadFailed(false), 8000);
    }
    if (data) saveSurveyRecord(data.email);
  };

  const handleDownloadClick = () => {
    setShowSurvey(true);
  };

  const handleSurveyConfirm = async (data: SurveyData) => {
    // POST 到 Google Apps Script（fire-and-forget，CORS 用 no-cors 模式）
    if (SURVEY_ENDPOINT) {
      const payload = new FormData();
      Object.entries(data).forEach(([k, v]) => payload.append(k, v));
      payload.append("record_count", String(sorted.length));
      payload.append("submitted_at", new Date().toISOString());
      fetch(SURVEY_ENDPOINT, { method: "POST", body: payload, mode: "no-cors" }).catch(() => {});
    }
    triggerDownload(data);
    setShowSurvey(false);
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
        <div className="flex items-center gap-3 px-4 py-2.5 bg-white border-b border-slate-100 flex-shrink-0">
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
          <button
            onClick={handleDownloadClick}
            className="flex items-center gap-1.5 btn-outline text-xs py-1.5 px-3 text-brand-700
                       border-brand-200 hover:bg-brand-50 transition-colors flex-shrink-0"
          >
            <Download size={13} />
            {t("table.export")}
          </button>
          <a
            href={`${import.meta.env.BASE_URL}data/data_dictionary.xlsx`}
            download
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-brand-600 transition-colors flex-shrink-0"
          >
            <FileSpreadsheet size={13} />
            {t("table.dictionary")}
          </a>
        </div>

        {/* 表格 */}
        <div className="flex-1 overflow-hidden p-3">
          <div className="card h-full overflow-auto">
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-slate-50/95 backdrop-blur z-10">
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
                  {col.developer || "Developer"}
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
                  const devRaw = (p.project_developer_en || p.project_developer || "").trim();
                  const developer =
                    !devRaw || ["unknown", "未知", "未披露", "n/a", "na", "-", "—"].includes(devRaw.toLowerCase())
                      ? "—"
                      : devRaw;
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
                      <td className="px-3 py-2 text-slate-600 max-w-xs truncate">{developer}</td>
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
        </div>

        {/* 分页 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 py-2 border-t border-slate-100
                          bg-white flex-shrink-0 text-xs text-slate-600">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="btn-outline disabled:opacity-40 py-1 px-2.5 rounded-full"
            >
              ←
            </button>
            <span>
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
              className="btn-outline disabled:opacity-40 py-1 px-2.5 rounded-full"
            >
              →
            </button>
          </div>
        )}
      </div>

      {/* 项目详情滑出抽屉 */}
      <ProjectDetailDrawer
        project={selectedProject}
        onClose={() => setSelectedProject(null)}
      />

      {/* 下载问卷弹窗 */}
      {showSurvey && (
        <DownloadSurveyModal
          rowCount={sorted.length}
          onConfirm={handleSurveyConfirm}
          onClose={() => setShowSurvey(false)}
        />
      )}

      {/* 下载失败提示（8 秒后自动消失，可手动关闭） */}
      {downloadFailed && (
        <div className="fixed bottom-5 right-5 z-50 max-w-sm flex items-start gap-2.5
                        bg-red-50 border border-red-200 text-red-700 rounded-xl shadow-lg
                        px-4 py-3 text-sm">
          <AlertCircle size={18} className="flex-shrink-0 mt-0.5 text-red-500" />
          <span className="flex-1">{t("table.downloadError")}</span>
          <button
            onClick={() => setDownloadFailed(false)}
            className="flex-shrink-0 text-red-400 hover:text-red-600 transition"
            aria-label="Close"
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
