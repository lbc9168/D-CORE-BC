import { useState, useMemo } from "react";
import { ComposableMap, Geographies, Geography, ZoomableGroup } from "react-simple-maps";
import type { Project, ColorBy } from "@/types";
import { valueToColor } from "@/utils/colorScale";
import { useLang } from "@/contexts/LanguageContext";
import { useProvinceGeo } from "@/hooks/useProvinceGeo";
import { hasCapacity } from "@/utils/capacityPolicy";
import { Loader2 } from "lucide-react";

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  content: string;
}

interface ProvinceMapProps {
  countryIso3: string;
  countryName: string;
  projects: Project[];            // 已过滤的该国项目
  colorBy: ColorBy;
  onProvinceClick: (provinceName: string | null) => void;  // null = 无省级信息
  selectedProvince: string | null;
}

/** 归一化省名：小写 + 去除末尾 "province" / "state" 等 */
function normalizeName(s: string): string {
  return s
    .toLowerCase()
    .replace(/\s+(province|state|region|prefecture|governorate|oblast|district)$/i, "")
    .trim();
}

export default function ProvinceMap({
  countryIso3,
  countryName,
  projects,
  colorBy,
  onProvinceClick,
  selectedProvince,
}: ProvinceMapProps) {
  const { t } = useLang();
  const geo = useProvinceGeo(countryIso3);
  const [tooltip, setTooltip] = useState<TooltipState>({
    visible: false, x: 0, y: 0, content: "",
  });

  /** 省名 → 聚合值 */
  const provinceValues = useMemo(() => {
    const map = new Map<string, number>();
    projects.forEach((p) => {
      if (!p.province) return;
      const key = normalizeName(p.province);
      if (colorBy === "count") {
        map.set(key, (map.get(key) ?? 0) + 1);
      } else {
        // 储能/输变电建设单位非 MW，装机容量统计时排除
        if (!hasCapacity(p.category)) return;
        map.set(key, (map.get(key) ?? 0) + (p.capacity_mw ?? 0));
      }
    });
    return map;
  }, [projects, colorBy]);

  const maxVal = useMemo(
    () => Math.max(0, ...Array.from(provinceValues.values())),
    [provinceValues],
  );

  if (geo.status === "loading") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-400">
        <Loader2 size={24} className="animate-spin" />
        <span className="text-sm">{t("map.provinceLoading")}</span>
      </div>
    );
  }

  if (geo.status === "unavailable" || !geo.data) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-400">
        <span className="text-2xl">🗺</span>
        <span className="text-sm">{t("map.provinceUnavailable")}</span>
        <span className="text-xs text-slate-300">{countryName} ({countryIso3})</span>
      </div>
    );
  }

  return (
    <div className="relative w-full h-full select-none">
      <ComposableMap
        projectionConfig={{ scale: 380 }}
        style={{ width: "100%", height: "100%" }}
      >
        <ZoomableGroup zoom={1} minZoom={0.5} maxZoom={8}>
          <Geographies geography={geo.data}>
            {({ geographies }) =>
              geographies.map((geo) => {
                const rawName =
                  (geo.properties?.["name"] as string | undefined) ??
                  (geo.properties?.["NAME"] as string | undefined) ?? "";
                const normName = normalizeName(rawName);
                const value = provinceValues.get(normName);
                const fill = valueToColor(value, maxVal);
                const isSelected = selectedProvince != null &&
                  normalizeName(selectedProvince) === normName;

                return (
                  <Geography
                    key={geo.rsmKey}
                    geography={geo}
                    fill={isSelected ? "#0284c7" : fill}
                    stroke="#fff"
                    strokeWidth={0.5}
                    style={{
                      default: { outline: "none" },
                      hover: {
                        fill: "#38bdf8",
                        cursor: "pointer",
                        outline: "none",
                      },
                      pressed: { outline: "none" },
                    }}
                    onMouseEnter={(e) => {
                      const label =
                        value != null
                          ? colorBy === "count"
                            ? `${rawName}\n${value} ${t("map.projects")}`
                            : `${rawName}\n${value.toFixed(0)} ${t("map.mw")}`
                          : `${rawName}\n${t("map.noData")}`;
                      setTooltip({ visible: true, x: e.clientX, y: e.clientY, content: label });
                    }}
                    onMouseLeave={() =>
                      setTooltip((prev) => ({ ...prev, visible: false }))
                    }
                    onClick={() => onProvinceClick(rawName)}
                  />
                );
              })
            }
          </Geographies>
        </ZoomableGroup>
      </ComposableMap>

      {tooltip.visible && (
        <div
          className="fixed z-50 pointer-events-none bg-slate-800 text-white text-xs px-2 py-1.5
                     rounded-lg shadow-lg whitespace-pre-line leading-relaxed"
          style={{ left: tooltip.x + 12, top: tooltip.y - 8 }}
        >
          {tooltip.content}
        </div>
      )}

      <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-white/80 backdrop-blur-sm
                      text-slate-500 text-xs px-3 py-1 rounded-full shadow-sm border border-slate-100">
        {t("map.clickProvince")}
      </div>
    </div>
  );
}
