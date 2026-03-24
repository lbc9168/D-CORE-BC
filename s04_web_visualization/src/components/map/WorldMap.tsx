import { useState, useMemo } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  ZoomableGroup,
} from "react-simple-maps";
import type { ColorBy } from "@/types";
import { valueToColor, buildColorBins } from "@/utils/colorScale";
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
  const [tooltip, setTooltip] = useState<TooltipState>({
    visible: false, x: 0, y: 0, content: "",
  });

  const colorValues = useMemo(() => Array.from(countryValues.values()), [countryValues]);
  const bins = useMemo(() => buildColorBins(colorValues, colorBy), [colorValues, colorBy]);

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
                const iso3 = getIso3FromNumericId(numericId);
                const value = countryValues.get(iso3);
                // hasData: 只要该国在 countryValues 中有记录，即视为有数据（value 可能为 0）
                const hasData = iso3 !== "" && countryValues.has(iso3);
                const fill = valueToColor(value, bins);
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
                        fill: hasData ? "#0369a1" : "#cbd5e1",
                        cursor: hasData ? "pointer" : "default",
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
                      if (hasData) onCountryClick(iso3, name);
                    }}
                  />
                );
              })
            }
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
