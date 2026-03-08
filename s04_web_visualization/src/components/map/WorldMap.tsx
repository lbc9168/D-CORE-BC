import { useState, useMemo } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  ZoomableGroup,
} from "react-simple-maps";
import type { ColorBy } from "@/types";
import { valueToColor, buildLegendStops } from "@/utils/colorScale";
import { NUMERIC_TO_ISO3 } from "@/utils/isoMapping";
import { useLang } from "@/contexts/LanguageContext";

// world-atlas 国家 TopoJSON（CDN，~80KB）
const GEO_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";

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
  const [tooltip, setTooltip] = useState<TooltipState>({
    visible: false, x: 0, y: 0, content: "",
  });

  const maxVal = useMemo(
    () => Math.max(0, ...Array.from(countryValues.values())),
    [countryValues],
  );

  const legendStops = buildLegendStops(maxVal, colorBy);

  return (
    <div className="relative w-full h-full select-none">
      <ComposableMap
        projectionConfig={{ scale: 147, center: [15, 10] }}
        style={{ width: "100%", height: "100%" }}
      >
        <ZoomableGroup zoom={1} minZoom={0.8} maxZoom={6}>
          <Geographies geography={GEO_URL}>
            {({ geographies }) =>
              geographies.map((geo) => {
                const numericId = String(geo.id ?? geo.properties?.["id"] ?? "");
                const iso3 = NUMERIC_TO_ISO3[numericId] ?? "";
                const value = countryValues.get(iso3);
                const fill = valueToColor(value, maxVal);
                const name =
                  (geo.properties?.["name"] as string | undefined) ?? iso3;

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
                        fill: iso3 && value ? "#0369a1" : "#cbd5e1",
                        cursor: iso3 && value ? "pointer" : "default",
                        outline: "none",
                      },
                      pressed: { outline: "none" },
                    }}
                    onMouseEnter={(e) => {
                      if (!iso3) return;
                      const label =
                        value != null
                          ? colorBy === "count"
                            ? `${name}\n${value} ${t("map.projects")}`
                            : `${name}\n${value.toFixed(0)} ${t("map.mw")}`
                          : `${name}\n${t("map.noData")}`;
                      setTooltip({
                        visible: true,
                        x: e.clientX,
                        y: e.clientY,
                        content: label,
                      });
                    }}
                    onMouseLeave={() =>
                      setTooltip((prev) => ({ ...prev, visible: false }))
                    }
                    onClick={() => {
                      if (iso3 && value) onCountryClick(iso3, name);
                    }}
                  />
                );
              })
            }
          </Geographies>
        </ZoomableGroup>
      </ComposableMap>

      {/* 悬浮提示 */}
      {tooltip.visible && (
        <div
          className="fixed z-50 pointer-events-none bg-slate-800 text-white text-xs px-2 py-1.5
                     rounded-lg shadow-lg whitespace-pre-line leading-relaxed"
          style={{ left: tooltip.x + 12, top: tooltip.y - 8 }}
        >
          {tooltip.content}
        </div>
      )}

      {/* 图例 */}
      {legendStops.length > 0 && (
        <div className="absolute bottom-4 left-4 bg-white/90 backdrop-blur-sm rounded-lg p-2.5
                        shadow-sm border border-slate-100 text-xs">
          <div className="text-slate-500 mb-1.5 font-medium">
            {colorBy === "count" ? t("map.byCount") : t("map.byCapacity")}
          </div>
          <div className="flex items-end gap-1">
            {legendStops.map((stop) => (
              <div key={stop.value} className="flex flex-col items-center gap-0.5">
                <div
                  className="w-7 h-3 rounded-sm"
                  style={{ backgroundColor: stop.color }}
                />
                <span className="text-slate-500" style={{ fontSize: "9px" }}>
                  {stop.label}
                </span>
              </div>
            ))}
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
