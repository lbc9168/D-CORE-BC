import { useState, useMemo, useEffect, useCallback, type WheelEvent } from "react";
import { ComposableMap, Geographies, Geography, ZoomableGroup } from "react-simple-maps";
import type { Project, ColorBy } from "@/types";
import { valueToColor, buildColorBins } from "@/utils/colorScale";
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
function normalizeName(s: unknown): string {
  return String(s ?? "")
    .toLowerCase()
    .replace(/\s+(province|state|region|prefecture|governorate|oblast|district)$/i, "")
    .trim();
}

function collectLngLatPoints(node: unknown, points: Array<[number, number]>): void {
  if (!Array.isArray(node)) return;
  if (
    node.length >= 2 &&
    typeof node[0] === "number" &&
    typeof node[1] === "number"
  ) {
    points.push([node[0], node[1]]);
    return;
  }
  node.forEach((child) => collectLngLatPoints(child, points));
}

const DEFAULT_CENTER: [number, number] = [0, 0];
const DEFAULT_ZOOM = 1;
const BASE_SCALE = 170;

/** 根据所有省份的坐标计算国家边框和合适的初始缩放 */
function computeCountryFit(geoData: unknown): { center: [number, number]; zoom: number } | null {
  const fc = geoData as { features?: Array<{ geometry?: { coordinates?: unknown } }> };
  if (!fc?.features || fc.features.length === 0) return null;

  const allPoints: Array<[number, number]> = [];
  fc.features.forEach((f) => {
    if (f?.geometry?.coordinates) {
      collectLngLatPoints(f.geometry.coordinates, allPoints);
    }
  });
  if (allPoints.length === 0) return null;

  let minLng = Infinity, maxLng = -Infinity, minLat = Infinity, maxLat = -Infinity;
  allPoints.forEach(([lng, lat]) => {
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  });
  if (!Number.isFinite(minLng) || !Number.isFinite(maxLng) || !Number.isFinite(minLat) || !Number.isFinite(maxLat)) {
    return null;
  }

  const center: [number, number] = [(minLng + maxLng) / 2, (minLat + maxLat) / 2];
  const lngSpan = Math.max(maxLng - minLng, 0.1);
  const latSpan = Math.max(maxLat - minLat, 0.1);
  // 取经纬度各自推算的缩放值的最小值，再乘以 0.65 留出边距
  const zoom = Math.min(Math.max(Math.min(360 / lngSpan, 180 / latSpan) * 0.65, 1), 6);

  return { center, zoom };
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
  const [projectionCenter, setProjectionCenter] = useState<[number, number]>(DEFAULT_CENTER);
  const [projectionScale, setProjectionScale] = useState(BASE_SCALE * DEFAULT_ZOOM);
  const [viewZoom, setViewZoom] = useState(1);
  const [hoveredProvince, setHoveredProvince] = useState<string>("");
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

  const colorValues = useMemo(() => Array.from(provinceValues.values()), [provinceValues]);
  const bins = useMemo(() => buildColorBins(colorValues, colorBy), [colorValues, colorBy]);
  const safeProjectionCenter = useMemo<[number, number]>(() => {
    if (!Number.isFinite(projectionCenter[0]) || !Number.isFinite(projectionCenter[1])) {
      return DEFAULT_CENTER;
    }
    return projectionCenter;
  }, [projectionCenter]);
  const safeProjectionScale = useMemo(() => {
    if (!Number.isFinite(projectionScale) || projectionScale <= 0) return BASE_SCALE;
    return Math.min(Math.max(projectionScale, 80), 2200);
  }, [projectionScale]);
  const hasProvinceFeatures = useMemo(() => {
    const fc = geo.data as { features?: unknown[] } | null;
    return Array.isArray(fc?.features) && fc.features.length > 0;
  }, [geo.data]);

  const safeFeatures = useMemo(() => {
    const fc = geo.data as { type?: unknown; features?: unknown[] } | null;
    if (!fc || fc.type !== "FeatureCollection" || !Array.isArray(fc.features)) return [] as Array<Record<string, unknown>>;
    const features = fc.features.filter(
      (f) => typeof f === "object" && f != null && "geometry" in (f as Record<string, unknown>),
    );
    return features as Array<Record<string, unknown>>;
  }, [geo.data]);

  // 切换国家时先重置到默认状态（等 geo 加载后再 fit）
  useEffect(() => {
    setProjectionCenter(DEFAULT_CENTER);
    setProjectionScale(BASE_SCALE * DEFAULT_ZOOM);
    setViewZoom(1);
  }, [countryIso3]);

  // geo 加载完成后自动 fit-to-bounds（解决小国地图太小/不居中问题）
  useEffect(() => {
    if (geo.status !== "ok" || !geo.data) return;
    try {
      const fit = computeCountryFit(geo.data);
      if (!fit) return;
      setProjectionCenter(fit.center);
      setProjectionScale(BASE_SCALE * fit.zoom);
    } catch (err) {
      // 极端几何数据下保底回退，避免整页白屏
      console.error("[ProvinceMap] computeCountryFit failed:", err);
      setProjectionCenter(DEFAULT_CENTER);
      setProjectionScale(BASE_SCALE * DEFAULT_ZOOM);
    }
  }, [geo.data, geo.status]);

  const handleProvinceMouseLeave = useCallback(() => {
    setHoveredProvince("");
    setTooltip((prev) => ({ ...prev, visible: false }));
  }, []);

  if (geo.status === "idle" || geo.status === "loading") {
    return (
      <div className="flex flex-col items-center justify-center w-full h-full gap-2 text-slate-400">
        <Loader2 size={24} className="animate-spin" />
        <span className="text-sm">{t("map.provinceLoading")}</span>
      </div>
    );
  }

  if (geo.status === "unavailable" || !geo.data || !hasProvinceFeatures || safeFeatures.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-400">
        <span className="text-2xl">🗺</span>
        <span className="text-sm">{t("map.provinceUnavailable")}</span>
        <span className="text-xs text-slate-300">{countryName} ({countryIso3})</span>
      </div>
    );
  }

  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setViewZoom((prev) => {
      const next = prev * Math.exp(-event.deltaY * 0.0015);
      return Math.min(Math.max(next, 0.8), 8);
    });
  };

  return (
    <div className="relative w-full h-full select-none" onWheel={handleWheel}>
      <ComposableMap
        projection="geoMercator"
        projectionConfig={{ scale: safeProjectionScale, center: safeProjectionCenter }}
        style={{ width: "100%", height: "100%", background: "#eeeeee" }}
      >
        <ZoomableGroup
          key={countryIso3}
          zoom={viewZoom}
          minZoom={0.8}
          maxZoom={8}
          disableZooming
        >
          <Geographies geography={safeFeatures}>
            {({ geographies }) => (
              <>
                {/* 第一遍：正常渲染所有省份，stroke 统一白色 */}
                {geographies.map((feature) => {
                  const rawName =
                    String(feature.properties?.["name"] ?? feature.properties?.["NAME"] ?? "");
                  const normName = normalizeName(rawName);
                  const value = provinceValues.get(normName);
                  const fill = valueToColor(value, bins);
                  const isSelected = selectedProvince != null &&
                    normalizeName(selectedProvince) === normName;

                  return (
                    <Geography
                      key={feature.rsmKey}
                      geography={feature}
                      fill={isSelected ? "#2F6562" : fill}
                      stroke="#fff"
                      strokeWidth={0.5}
                      style={{
                        default: { outline: "none" },
                        hover: { fill: "#5A9896", cursor: "pointer", outline: "none" },
                        pressed: { outline: "none" },
                      }}
                      onMouseEnter={(e) => {
                        setHoveredProvince(normName);
                        const label =
                          value != null
                            ? colorBy === "count"
                              ? `${rawName}\n${value} ${t("map.projects")}`
                              : `${rawName}\n${value.toFixed(0)} ${t("map.mw")}`
                            : `${rawName}\n${t("map.noData")}`;
                        setTooltip({ visible: true, x: e.clientX, y: e.clientY, content: label });
                      }}
                      onMouseLeave={handleProvinceMouseLeave}
                      onClick={() => { onProvinceClick(rawName); }}
                    />
                  );
                })}

                {/* 第二遍：hovered 省份覆盖渲染在最顶层，stroke 完整显示 */}
                {/* pointerEvents none：鼠标事件穿透到第一遍，避免 leave/enter 循环闪烁 */}
                <g style={{ pointerEvents: "none" }}>
                  {hoveredProvince !== "" && geographies
                    .filter((feature) => {
                      const rawName =
                        String(feature.properties?.["name"] ?? feature.properties?.["NAME"] ?? "");
                      return normalizeName(rawName) === hoveredProvince;
                    })
                    .map((feature) => {
                      const rawName =
                        String(feature.properties?.["name"] ?? feature.properties?.["NAME"] ?? "");
                      const normName = normalizeName(rawName);
                      const value = provinceValues.get(normName);
                      const fill = valueToColor(value, bins);
                      const isSelected = selectedProvince != null &&
                        normalizeName(selectedProvince) === normName;
                      return (
                        <Geography
                          key={`hover-${feature.rsmKey}`}
                          geography={feature}
                          fill={isSelected ? "#2F6562" : fill}
                          stroke="#1e293b"
                          strokeWidth={1.0}
                          style={{
                            default: { outline: "none" },
                            hover: { fill: isSelected ? "#2F6562" : fill, outline: "none" },
                            pressed: { outline: "none" },
                          }}
                        />
                      );
                    })
                  }
                </g>
              </>
            )}
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
