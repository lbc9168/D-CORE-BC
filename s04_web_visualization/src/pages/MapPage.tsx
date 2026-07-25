import { useState, useMemo, useEffect } from "react";
import { ArrowLeft, MapPin, Loader2 } from "lucide-react";
import { useData, useCountryStats } from "@/contexts/DataContext";
import { useDomain } from "@/contexts/DomainContext";
import { useLang } from "@/contexts/LanguageContext";
import DomainSwitcher from "@/components/filters/DomainSwitcher";
import FilterBar from "@/components/filters/FilterBar";
import WorldMap from "@/components/map/WorldMap";
import ProvinceMap from "@/components/map/ProvinceMap";
import ProvinceMapErrorBoundary from "@/components/map/ProvinceMapErrorBoundary";
import ProjectList from "@/components/projects/ProjectList";
import TxMfProjectList from "@/components/projects/TxMfProjectList";
import ProjectDetailDrawer from "@/components/projects/ProjectDetailDrawer";
import { isStorageLens, isStorageMetric } from "@/utils/domains";
import type { MapLevel, MetricKey, Project, TxMfProject } from "@/types";

/** Map 页：视角条(域/度量) + 筛选栏 + 世界/省级地图 + 项目列表 + 详情抽屉 */
export default function MapPage() {
  const { filteredProjects, filteredTxMf, txmfLoading, loading, error } = useData();
  const { domain, metric } = useDomain();
  const { t } = useLang();

  const [mapLevel, setMapLevel] = useState<MapLevel>("world");
  const [selectedCountry, setSelectedCountry] = useState<{ iso3: string; name: string } | null>(null);
  const [selectedProvince, setSelectedProvince] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | TxMfProject | null>(null);

  const isTxMfDomain = domain === "txmf";
  // txmf 域度量固定 count（数据硬约束）；发电域用视角条选中的度量
  const effectiveMetric: MetricKey = isTxMfDomain ? "count" : metric;
  // 储能透镜：发电域选中储能度量时，地图/列表聚焦储能相关子集（地图值 ⊆ 列表，两者同源一致）
  const storageLensOn = !isTxMfDomain && isStorageMetric(metric);

  const rows = useMemo<Array<Project | TxMfProject>>(() => {
    if (isTxMfDomain) return filteredTxMf;
    if (storageLensOn) return filteredProjects.filter(isStorageLens);
    return filteredProjects;
  }, [isTxMfDomain, storageLensOn, filteredProjects, filteredTxMf]);

  const countryValues = useCountryStats(rows, effectiveMetric);

  // 当前国家的项目
  const countryRows = useMemo(
    () =>
      selectedCountry
        ? rows.filter((p) => p.country_iso === selectedCountry.iso3)
        : [],
    [rows, selectedCountry],
  );

  // 当前省/无省级 的项目
  const panelRows = useMemo(() => {
    if (!selectedCountry) return rows;
    if (selectedProvince === "__no_province__") {
      return countryRows.filter((p) => !p.province);
    }
    if (selectedProvince) {
      const normProv = (s: string) =>
        s.toLowerCase()
          .replace(/\s+(province|state|region|prefecture|governorate|oblast|district)$/i, "")
          .trim();
      const norm = normProv(selectedProvince);
      return countryRows.filter(
        (p) => p.province && normProv(p.province) === norm,
      );
    }
    return countryRows;
  }, [countryRows, selectedCountry, selectedProvince, rows]);

  // 切域时清空地图/列表/抽屉选择，避免跨域状态错位（如抽屉里还开着别域的项目）
  useEffect(() => {
    setMapLevel("world");
    setSelectedCountry(null);
    setSelectedProvince(null);
    setSelectedProject(null);
  }, [domain]);

  // 点击国家
  const handleCountryClick = (iso3: string, name: string) => {
    setSelectedCountry({ iso3, name });
    setSelectedProvince(null);
    setSelectedProject(null);
    setMapLevel("province");
  };

  // 点击省份
  const handleProvinceClick = (prov: string | null) => {
    setSelectedProvince(prov ?? "__no_province__");
    setSelectedProject(null);
  };

  // 返回世界地图
  const handleBack = () => {
    setMapLevel("world");
    setSelectedCountry(null);
    setSelectedProvince(null);
    setSelectedProject(null);
  };

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-slate-400">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm">{t("common.loading")}</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center text-slate-400">
        <div className="text-sm text-red-500">{t("common.error")}</div>
      </div>
    );
  }

  const selectedProjectId = selectedProject?.project_id ?? null;
  const showTxMfSpinner = isTxMfDomain && txmfLoading;

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* 视角条（域切换 + 域内度量选择器，V3 设计） */}
      <DomainSwitcher />

      {/* 筛选栏 */}
      <FilterBar />

      {/* 主区域 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 左：地图区域 */}
        <div className="flex-1 relative overflow-hidden bg-[#eeeeee]">
          {/* 省级返回按钮 */}
          {mapLevel === "province" && (
            <div className="absolute top-3 left-3 z-20 flex items-center gap-2">
              <button onClick={handleBack} className="btn-outline text-xs bg-white/90 backdrop-blur-sm">
                <ArrowLeft size={13} />
                {t("map.backToWorld")}
              </button>
              {selectedCountry && (
                <span className="text-xs font-medium text-slate-700 bg-white/90 backdrop-blur-sm
                                 px-2.5 py-1 rounded-lg border border-slate-200 shadow-sm">
                  {selectedCountry.name}
                </span>
              )}
            </div>
          )}

          {/* 地图本体 */}
          <div className="w-full h-full">
            {mapLevel === "world" ? (
              <WorldMap
                countryValues={countryValues}
                metric={effectiveMetric}
                onCountryClick={handleCountryClick}
              />
            ) : selectedCountry ? (
              <ProvinceMapErrorBoundary
                countryIso3={selectedCountry.iso3}
                countryName={selectedCountry.name}
              >
                <ProvinceMap
                  countryIso3={selectedCountry.iso3}
                  countryName={selectedCountry.name}
                  projects={countryRows}
                  metric={effectiveMetric}
                  onProvinceClick={handleProvinceClick}
                  selectedProvince={selectedProvince}
                />
              </ProvinceMapErrorBoundary>
            ) : null}
          </div>

          {/* 省级视图：无省级信息按钮 */}
          {mapLevel === "province" && (
            <button
              onClick={() => handleProvinceClick(null)}
              className={`absolute bottom-4 right-4 btn-outline bg-white/90 backdrop-blur-sm text-xs
                         ${selectedProvince === "__no_province__" ? "border-brand-400 text-brand-600" : ""}`}
            >
              <MapPin size={13} />
              {t("map.noProvince")}
              {countryRows.filter((p) => !p.province).length > 0 && (
                <span className="badge bg-slate-100 text-slate-600">
                  {countryRows.filter((p) => !p.province).length}
                </span>
              )}
            </button>
          )}
        </div>

        {/* 右：项目列表（世界层=全部筛选结果；国家/省层=对应子集） */}
        <div className="w-[26rem] max-w-[42vw] min-w-[20rem] border-l border-slate-200 flex flex-col overflow-hidden bg-slate-50 flex-shrink-0">
          <div className="flex flex-col flex-1 overflow-hidden p-2">
            <div className="card flex flex-col flex-1 overflow-hidden">
              <div className="px-3 py-2.5 border-b border-slate-100 font-medium text-sm text-slate-700 flex items-center justify-between gap-1.5">
                <span>
                  {selectedProvince === "__no_province__"
                    ? t("map.noProvince")
                    : selectedProvince ?? selectedCountry?.name ??
                      (storageLensOn ? t("projectList.storageTitle") : t("projectList.title"))}
                </span>
                <span className="badge bg-slate-100 text-slate-500">{panelRows.length}</span>
              </div>
              <div className="flex-1 overflow-hidden">
                {showTxMfSpinner ? (
                  <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
                    <Loader2 size={20} className="animate-spin" />
                    <span className="text-sm">{t("common.loading")}</span>
                  </div>
                ) : isTxMfDomain ? (
                  <TxMfProjectList
                    projects={panelRows as TxMfProject[]}
                    onSelect={setSelectedProject}
                    selectedId={selectedProjectId}
                  />
                ) : (
                  <ProjectList
                    projects={panelRows as Project[]}
                    onSelect={setSelectedProject}
                    selectedId={selectedProjectId}
                    variant={storageLensOn ? "storage" : "default"}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 项目详情滑出抽屉（覆盖层，不挤占布局） */}
      <ProjectDetailDrawer
        project={selectedProject}
        onClose={() => setSelectedProject(null)}
      />
    </div>
  );
}
