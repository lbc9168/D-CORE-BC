import { useState, useMemo } from "react";
import { ArrowLeft, MapPin } from "lucide-react";
import { useData, useCountryStats } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import FilterBar from "@/components/filters/FilterBar";
import WorldMap from "@/components/map/WorldMap";
import ProvinceMap from "@/components/map/ProvinceMap";
import ProvinceMapErrorBoundary from "@/components/map/ProvinceMapErrorBoundary";
import ProjectList from "@/components/projects/ProjectList";
import type { ColorBy, MapLevel, Project } from "@/types";

export default function MapPage() {
  const { filteredProjects, loading, error } = useData();
  const { t } = useLang();

  const [colorBy, setColorBy] = useState<ColorBy>("count");
  const [mapLevel, setMapLevel] = useState<MapLevel>("world");
  const [selectedCountry, setSelectedCountry] = useState<{ iso3: string; name: string } | null>(null);
  const [selectedProvince, setSelectedProvince] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

  const countryValues = useCountryStats(filteredProjects, colorBy);

  // 当前国家的项目
  const countryProjects = useMemo(
    () =>
      selectedCountry
        ? filteredProjects.filter((p) => p.country_iso === selectedCountry.iso3)
        : [],
    [filteredProjects, selectedCountry],
  );

  // 当前省/无省级 的项目
  const panelProjects = useMemo(() => {
    if (!selectedCountry) return filteredProjects;
    if (selectedProvince === "__no_province__") {
      return countryProjects.filter((p) => !p.province);
    }
    if (selectedProvince) {
      const normProv = (s: string) =>
        s.toLowerCase()
          .replace(/\s+(province|state|region|prefecture|governorate|oblast|district)$/i, "")
          .trim();
      const norm = normProv(selectedProvince);
      return countryProjects.filter(
        (p) => p.province && normProv(p.province) === norm,
      );
    }
    return countryProjects;
  }, [countryProjects, selectedCountry, selectedProvince, filteredProjects]);

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

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* 筛选栏 */}
      <FilterBar colorBy={colorBy} onColorByChange={setColorBy} />

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
                colorBy={colorBy}
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
                  projects={countryProjects}
                  colorBy={colorBy}
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
              {countryProjects.filter((p) => !p.province).length > 0 && (
                <span className="badge bg-slate-100 text-slate-600">
                  {countryProjects.filter((p) => !p.province).length}
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
                    : selectedProvince ?? selectedCountry?.name ?? t("projectList.title")}
                </span>
                <span className="badge bg-slate-100 text-slate-500">{panelProjects.length}</span>
              </div>
              <div className="flex-1 overflow-hidden">
                <ProjectList
                  projects={panelProjects}
                  onSelect={setSelectedProject}
                  selectedId={selectedProjectId}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
