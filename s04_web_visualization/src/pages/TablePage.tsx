import { useState, useMemo, useEffect } from "react";
import { Search, Download, FileSpreadsheet, AlertCircle, X } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useDomain } from "@/contexts/DomainContext";
import { useLang } from "@/contexts/LanguageContext";
import type { Project, TxMfProject } from "@/types";
import { CATEGORIES, STAGES, STAGE_COLORS, VIZ_GROUPS } from "@/types";
import { stageToGroup } from "@/utils/enums";
import FilterBar from "@/components/filters/FilterBar";
import DomainSwitcher from "@/components/filters/DomainSwitcher";
import ProjectDetailDrawer from "@/components/projects/ProjectDetailDrawer";
import { txmfScaleLabel } from "@/components/projects/TxMfProjectList";
import { TX_CATEGORY_KEY, MF_CATEGORY_KEY } from "@/utils/domains";
import { formatCountryName } from "@/utils/display";
import { downloadCSV } from "@/utils/csvExport";
import DownloadSurveyModal, { saveSurveyRecord, type SurveyData } from "@/components/DownloadSurveyModal";
import TechnologyTagBadges from "@/components/projects/TechnologyTagBadges";

const SURVEY_ENDPOINT = import.meta.env.VITE_SURVEY_ENDPOINT as string | undefined;

const PAGE_SIZE = 50;

/** 储能合并列：MW/MWh 有值拼接（不限类别——综合能源披露了储能字段同样显示） */
function storageCellLabel(p: Project): string | null {
  const parts: string[] = [];
  if (p.storage_power_mw != null) parts.push(`${p.storage_power_mw.toFixed(0)} MW`);
  if (p.storage_energy_mwh != null) parts.push(`${p.storage_energy_mwh.toFixed(0)} MWh`);
  return parts.length ? parts.join(" · ") : null;
}

/** txmf 表可排序键 → 取值器（专表 union 无法直接按 keyof 索引） */
const TXMF_SORT_GETTERS: Record<string, (p: TxMfProject) => string | number | null> = {
  project_id: (p) => p.project_id,
  country: (p) => p.country_en || p.country,
  event_year: (p) => p.event_year,
};

export default function TablePage() {
  const { filteredProjects, filteredTxMf, allProjects, txProjects, mfProjects, loading, error } = useData();
  const { domain } = useDomain();
  const { t, T } = useLang();
  const isTxMf = domain === "txmf";

  // 下载恒为全量单文件，与当前筛选/域无关 → 弹窗显示文件里的项目总数（发电 + 输变电·制造业）
  const csvProjectCount = allProjects.length + txProjects.length + mfProjects.length;

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<string>("display_order");
  const [sortAsc, setSortAsc] = useState(true);
  const [selectedProject, setSelectedProject] = useState<Project | TxMfProject | null>(null);
  const [showSurvey, setShowSurvey] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);

  // 切域重置搜索/分页/排序/选中（两套表列组不同，状态不跨域携带）
  useEffect(() => {
    setSearch("");
    setPage(0);
    setSortKey(isTxMf ? "project_id" : "display_order");
    setSortAsc(true);
    setSelectedProject(null);
  }, [domain, isTxMf]);

  // 搜索（主表）
  const searchedMain = useMemo(() => {
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

  // 搜索（txmf 专表：名称/国家/参与实体）
  const searchedTxMf = useMemo(() => {
    if (!search.trim()) return filteredTxMf;
    const q = search.toLowerCase();
    return filteredTxMf.filter(
      (p) =>
        p.project_name?.toLowerCase().includes(q) ||
        p.project_name_en?.toLowerCase().includes(q) ||
        p.country?.toLowerCase().includes(q) ||
        (p.country_en ?? "").toLowerCase().includes(q) ||
        p.all_entities?.some((e) => (e.name_en || e.name || "").toLowerCase().includes(q)),
    );
  }, [filteredTxMf, search]);

  // 排序（主表）
  const sortedMain = useMemo(() => {
    const key = sortKey as keyof Project;
    return [...searchedMain].sort((a, b) => {
      const av = a[key] as string | number | null;
      const bv = b[key] as string | number | null;
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortAsc ? cmp : -cmp;
    });
  }, [searchedMain, sortKey, sortAsc]);

  // 排序（txmf 专表）
  const sortedTxMf = useMemo(() => {
    const get = TXMF_SORT_GETTERS[sortKey] ?? TXMF_SORT_GETTERS.project_id;
    return [...searchedTxMf].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortAsc ? cmp : -cmp;
    });
  }, [searchedTxMf, sortKey, sortAsc]);

  const totalCount = isTxMf ? sortedTxMf.length : sortedMain.length;
  const totalPages = Math.ceil(totalCount / PAGE_SIZE);
  const pageMain = sortedMain.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const pageTxMf = sortedTxMf.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const handleSort = (key: string) => {
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
      payload.append("record_count", String(csvProjectCount));
      payload.append("submitted_at", new Date().toISOString());
      fetch(SURVEY_ENDPOINT, { method: "POST", body: payload, mode: "no-cors" }).catch(() => {});
    }
    triggerDownload(data);
    setShowSurvey(false);
  };

  const SortBtn = ({ k, label }: { k: string; label: string }) => (
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
        {/* 视角条（数据域切换；Table 页不消费地图度量，隐藏度量胶囊） */}
        <DomainSwitcher showMetrics={false} />
        <FilterBar />

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
            {t("table.total", { count: totalCount })}
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

        {/* 表格（按域两套列组：发电主表 / 输变电·制造业专表） */}
        <div className="flex-1 overflow-hidden p-3">
          <div className="card h-full overflow-auto">
          {!isTxMf ? (
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
                <th className="text-right px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  <SortBtn k="storage_energy_mwh" label={col.storage || "Storage"} />
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
              {pageMain.length === 0 ? (
                <tr>
                  <td colSpan={11} className="py-12 text-center text-slate-400">
                    {t("table.empty")}
                  </td>
                </tr>
              ) : (
                pageMain.map((p) => {
                  const cat = CATEGORIES[p.category];
                  const stageColor = STAGE_COLORS[p.latest_stage] ?? "#94a3b8";
                  const name = p.project_name_en || p.project_name;
                  const devRaw = (p.project_developer_en || "").trim();
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
                        {p.subcategory_en && (
                          <span
                            className="badge border bg-white ml-1"
                            style={{
                              color: cat?.color ?? "#64748B",
                              borderColor: (cat?.color ?? "#64748B") + "66",
                              fontSize: "10px",
                            }}
                          >
                            {p.subcategory_en}
                          </span>
                        )}
                        <TechnologyTagBadges project={p} compact className="ml-1" />
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600 whitespace-nowrap">
                        {p.capacity_mw?.toFixed(0) ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600 whitespace-nowrap">
                        {storageCellLabel(p) ?? "—"}
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
          ) : (
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
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  {col.specs || "Specs"}
                </th>
                <th className="text-left px-3 py-2.5 font-medium text-slate-500 whitespace-nowrap">
                  {col.stage || "Stage"}
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
              {pageTxMf.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-12 text-center text-slate-400">
                    {t("table.empty")}
                  </td>
                </tr>
              ) : (
                pageTxMf.map((p) => {
                  const cat = CATEGORIES[p.kind === "tx" ? TX_CATEGORY_KEY : MF_CATEGORY_KEY];
                  const grp = stageToGroup(p.latest_stage);
                  const grpMeta = grp ? VIZ_GROUPS[grp] : null;
                  const stageColor = grpMeta?.color ?? "#94a3b8";
                  const name = p.project_name_en || p.project_name;
                  const firstEntity = p.all_entities?.[0];
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
                      <td className="px-3 py-2 text-slate-800 line-clamp-2 max-w-xs">{name}</td>
                      <td className="px-3 py-2 text-slate-600 whitespace-nowrap">
                        {formatCountryName(p.country, p.country_iso, "en")}
                      </td>
                      <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                        {p.province ?? "—"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {cat && (
                          <span
                            className="badge text-white"
                            style={{ backgroundColor: cat.color, fontSize: "10px" }}
                          >
                            {cat.en}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-slate-600 whitespace-nowrap">
                        {txmfScaleLabel(p) ?? "—"}
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
                          {grpMeta?.en ?? "—"}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600">{p.event_year}</td>
                      <td className="px-3 py-2 text-slate-600 max-w-xs truncate">
                        {firstEntity ? firstEntity.name_en || firstEntity.name : "—"}
                      </td>
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
          )}
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
          rowCount={csvProjectCount}
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
