import { useState, useMemo, useCallback, type WheelEvent } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  ZoomableGroup,
} from "react-simple-maps";
import type { ColorBy } from "@/types";
import { valueToColor, buildColorBins, EMPTY_COLOR } from "@/utils/colorScale";
import { getIso3FromNumericId } from "@/utils/isoMapping";
import { useLang } from "@/contexts/LanguageContext";

// world-atlas 国家 TopoJSON（本地，避免 CDN 访问限制）
const GEO_URL = `${import.meta.env.BASE_URL}data/countries-110m.json`;

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  content: string;
}

interface WorldMapProps {
  countryValues: Map<string, number>;   // iso3 → value
  colorBy: ColorBy;
  onCountryClick: (iso3: string, countryName: string) => void;
}

export default function WorldMap({ countryValues, colorBy, onCountryClick }: WorldMapProps) {
  const { t } = useLang();
  const [mapZoom, setMapZoom] = useState(1);
  const [hoveredIso3, setHoveredIso3] = useState<string>("");
  const [tooltip, setTooltip] = useState<TooltipState>({
    visible: false, x: 0, y: 0, content: "",
  });

  const colorValues = useMemo(() => Array.from(countryValues.values()), [countryValues]);
  const bins = useMemo(() => buildColorBins(colorValues, colorBy), [colorValues, colorBy]);

  const handleMouseLeave = useCallback(() => {
    setHoveredIso3("");
    setTooltip((prev) => ({ ...prev, visible: false }));
  }, []);

  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setMapZoom((prev) => {
      const next = prev * Math.exp(-event.deltaY * 0.0015);
      return Math.min(Math.max(next, 0.8), 6);
    });
  };

  return (
    <div className="relative w-full h-full select-none" onWheel={handleWheel}>
      <ComposableMap
        projectionConfig={{ scale: 147, center: [15, 10] }}
        style={{ width: "100%", height: "100%", background: "#eeeeee" }}
      >
        <ZoomableGroup
          zoom={mapZoom}
          minZoom={0.8}
          maxZoom={6}
          disableZooming
        >
          <Geographies geography={GEO_URL}>
            {({ geographies }) => {
              const visibleGeos = geographies.filter((geo) => {
                const rawName = (geo.properties?.["name"] as string | undefined) ?? "";
                const iso3 = getIso3FromNumericId(String(geo.id ?? geo.properties?.["id"] ?? ""));
                return iso3 !== "ATA" && rawName.toLowerCase() !== "antarctica";
              });

              return (
                <>
                  {/* 第一遍：正常渲染所有国家，stroke 统一白色 */}
                  {visibleGeos.map((geo) => {
                    const numericId = String(geo.id ?? geo.properties?.["id"] ?? "");
                    const iso3 = getIso3FromNumericId(numericId);
                    const value = countryValues.get(iso3);
                    const hasData = iso3 !== "" && countryValues.has(iso3);
                    const fill = valueToColor(value, bins);
                    const rawName = (geo.properties?.["name"] as string | undefined) ?? iso3;
                    const name = iso3 === "CHN" ? "China" : rawName;

                    return (
                      <Geography
                        key={geo.rsmKey}
                        geography={geo}
                        fill={fill}
                        stroke="#fff"
                        strokeWidth={0.4}
                        style={{
                          default: { outline: "none" },
                          hover: {
                            fill: fill,
                            cursor: hasData ? "pointer" : "default",
                            outline: "none",
                          },
                          pressed: { outline: "none" },
                        }}
                        onMouseEnter={(e) => {
                          if (!iso3) return;
                          setHoveredIso3(iso3);
                          const label =
                            value != null
                              ? colorBy === "count"
                                ? `${name}\n${value} ${t("map.projects")}`
                                : `${name}\n${value.toFixed(0)} ${t("map.mw")}`
                              : `${name}\n${t("map.noData")}`;
                          setTooltip({ visible: true, x: e.clientX, y: e.clientY, content: label });
                        }}
                        onMouseLeave={handleMouseLeave}
                        onClick={() => { if (hasData) onCountryClick(iso3, name); }}
                      />
                    );
                  })}

                  {/* 第二遍：hovered 国家覆盖渲染在最顶层，stroke 完整显示 */}
                  {/* pointerEvents none：鼠标事件穿透到第一遍，避免 leave/enter 循环闪烁 */}
                  <g style={{ pointerEvents: "none" }}>
                    {hoveredIso3 !== "" && visibleGeos
                      .filter((geo) => {
                        const iso3 = getIso3FromNumericId(String(geo.id ?? geo.properties?.["id"] ?? ""));
                        return iso3 === hoveredIso3;
                      })
                      .map((geo) => {
                        const iso3 = getIso3FromNumericId(String(geo.id ?? geo.properties?.["id"] ?? ""));
                        const value = countryValues.get(iso3);
                        const fill = valueToColor(value, bins);
                        return (
                          <Geography
                            key={`hover-${geo.rsmKey}`}
                            geography={geo}
                            fill={fill}
                            stroke="#1e293b"
                            strokeWidth={1.2}
                            style={{
                              default: { outline: "none" },
                              hover: { fill: fill, outline: "none" },
                              pressed: { outline: "none" },
                            }}
                          />
                        );
                      })
                    }
                  </g>
                </>
              );
            }}
          </Geographies>
        </ZoomableGroup>
      </ComposableMap>

      {/* 悬浮提示（带越界检测） */}
      {tooltip.visible && (
        <div
          className="fixed z-50 pointer-events-none bg-slate-800 text-white text-xs px-2 py-1.5
                     rounded-lg shadow-lg whitespace-pre-line leading-relaxed"
          style={{
            left: tooltip.x + 12 + 160 > window.innerWidth
              ? tooltip.x - 164
              : tooltip.x + 12,
            top: tooltip.y - 8 + 52 > window.innerHeight
              ? tooltip.y - 60
              : tooltip.y - 8,
          }}
        >
          {tooltip.content}
        </div>
      )}

      {/* 图例 */}
      {bins.length > 0 && (
        <div className="absolute bottom-4 left-4 bg-white/90 backdrop-blur-sm rounded-lg p-2.5
                        shadow-sm border border-slate-100 text-xs">
          <div className="text-slate-500 mb-1.5 font-medium">
            {colorBy === "count" ? t("map.byCount") : t("map.byCapacity")}
          </div>
          <div className="flex flex-col gap-1.5">
            {bins.map((bin) => (
              <div key={bin.label} className="flex items-center gap-2">
                <div
                  className="w-7 h-3 rounded-sm flex-shrink-0"
                  style={{ backgroundColor: bin.color }}
                />
                <span className="text-slate-500 leading-none" style={{ fontSize: "10px" }}>
                  {bin.label}
                </span>
              </div>
            ))}
            {/* 无数据说明项：与数据分档用细分隔线隔开 */}
            <div className="flex items-center gap-2 mt-0.5 pt-1.5 border-t border-slate-100">
              <div
                className="w-7 h-3 rounded-sm flex-shrink-0"
                style={{ backgroundColor: EMPTY_COLOR }}
              />
              <span className="text-slate-500 leading-none" style={{ fontSize: "10px" }}>
                {t("map.noData")}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* 提示文字 */}
      <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-white/80 backdrop-blur-sm
                      text-slate-500 text-xs px-3 py-1 rounded-full shadow-sm border border-slate-100">
        {t("map.clickCountry")}
      </div>
    </div>
  );
}
