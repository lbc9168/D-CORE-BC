import { useRef, useState, useEffect, useMemo } from "react";
import { RotateCcw, ChevronDown, Globe } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import { CATEGORIES, VIZ_GROUPS, VIZ_GROUP_KEYS } from "@/types";
import type { ColorBy, RegionFilter, VizGroup } from "@/types";
import { isHiddenCategory } from "@/utils/enums";
import { CONTINENTS, CONTINENT_SUBREGIONS } from "@/utils/continentMapping";

interface FilterBarProps {
  colorBy: ColorBy;
  onColorByChange: (v: ColorBy) => void;
}

/** 点击触发多选下拉（点击外部关闭） */
function MultiSelect({
  label,
  options,
  selected,
  onChange,
  renderLabel,
  allLabel,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (v: string[]) => void;
  renderLabel?: (v: string) => string;
  allLabel: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const toggle = (v: string) =>
    selected.includes(v) ? onChange(selected.filter((x) => x !== v)) : onChange([...selected, v]);

  // 点击外部关闭
  useEffect(() => {
    if (!isOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [isOpen]);

  // Escape 键关闭
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [isOpen]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setIsOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-slate-600 hover:text-slate-900
                   bg-white border border-slate-200 rounded-lg px-2.5 py-1.5
                   transition-all hover:border-brand-300 hover:shadow-sm"
      >
        <span>{label}</span>
        {selected.length > 0 && (
          <span className="badge bg-brand-100 text-brand-700">{selected.length}</span>
        )}
        <ChevronDown size={12} className={`text-slate-400 transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 z-40
                        bg-white border border-slate-200 rounded-xl shadow-lg py-1.5 min-w-36">
          <button
            onClick={() => onChange([])}
            className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 ${
              selected.length === 0 ? "text-brand-600 font-medium" : "text-slate-600"
            }`}
          >
            {allLabel}
          </button>
          <div className="border-t border-slate-100 my-1" />
          {options.map((opt) => (
            <label
              key={opt}
              className="flex items-center gap-2 px-3 py-1.5 text-xs text-slate-700
                         hover:bg-slate-50 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={selected.includes(opt)}
                onChange={() => toggle(opt)}
                className="accent-brand-600"
              />
              {renderLabel ? renderLabel(opt) : opt}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** 二级大洲/小区选择器 */
function RegionSelect({
  value,
  onChange,
}: {
  value: RegionFilter | null;
  onChange: (v: RegionFilter | null) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [expandedContinent, setExpandedContinent] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [isOpen]);

  const label = value
    ? value.subregion
      ? `${value.continent} · ${value.subregion}`
      : value.continent
    : "Region";

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setIsOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-slate-600 hover:text-slate-900
                   bg-white border border-slate-200 rounded-lg px-2.5 py-1.5
                   transition-all hover:border-brand-300 hover:shadow-sm"
      >
        <Globe size={11} className="text-slate-400" />
        <span className={value ? "text-brand-700 font-medium" : ""}>{label}</span>
        <ChevronDown size={12} className={`text-slate-400 transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 z-40
                        bg-white border border-slate-200 rounded-xl shadow-lg py-1.5 min-w-48">
          <button
            onClick={() => { onChange(null); setIsOpen(false); }}
            className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 ${
              !value ? "text-brand-600 font-medium" : "text-slate-600"
            }`}
          >
            All Regions
          </button>
          <div className="border-t border-slate-100 my-1" />
          {CONTINENTS.map((continent) => (
            <div key={continent}>
              <div className="flex items-center">
                <button
                  onClick={() => { onChange({ continent, subregion: null }); setIsOpen(false); }}
                  className={`flex-1 text-left px-3 py-1.5 text-xs hover:bg-slate-50 ${
                    value?.continent === continent && !value.subregion
                      ? "text-brand-600 font-medium"
                      : "text-slate-700"
                  }`}
                >
                  {continent}
                </button>
                <button
                  onClick={() => setExpandedContinent((c) => (c === continent ? null : continent))}
                  className="px-2 py-1.5 text-slate-400 hover:text-slate-600"
                >
                  <ChevronDown
                    size={11}
                    className={`transition-transform ${expandedContinent === continent ? "rotate-180" : ""}`}
                  />
                </button>
              </div>
              {expandedContinent === continent && (
                <div className="pl-4 border-l-2 border-brand-100 ml-3">
                  {CONTINENT_SUBREGIONS[continent]?.map((sub) => (
                    <button
                      key={sub}
                      onClick={() => { onChange({ continent, subregion: sub }); setIsOpen(false); }}
                      className={`w-full text-left px-3 py-1 text-xs hover:bg-slate-50 ${
                        value?.continent === continent && value.subregion === sub
                          ? "text-brand-600 font-medium"
                          : "text-slate-500"
                      }`}
                    >
                      {sub}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 单柄参考年滑块（"截至 Y"语义）。 */
function SingleYearSlider({
  minYear,
  maxYear,
  value,
  onChange,
}: {
  minYear: number;
  maxYear: number;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <input
      type="range"
      min={minYear}
      max={maxYear}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="single-year-input"
      style={{ width: "120px" }}
    />
  );
}

export default function FilterBar({ colorBy, onColorByChange }: FilterBarProps) {
  const { filter, setFilter, resetFilter, availableYears } = useData();
  const { t } = useLang();

  const minYear = availableYears[0] ?? 2022;   // 数据集起始年兜底（availableYears 为空时）
  const maxYear = availableYears[availableYears.length - 1] ?? new Date().getFullYear();
  const currentYear = filter.refYear ?? maxYear;
  const categoryOptions = useMemo(
    () => Object.keys(CATEGORIES).filter((k) => !isHiddenCategory(k)),
    [],
  );

  // 清理旧筛选状态中已隐藏的类别
  useEffect(() => {
    if (filter.categories.every((cat) => categoryOptions.includes(cat))) return;
    setFilter((f) => ({ ...f, categories: f.categories.filter((cat) => categoryOptions.includes(cat)) }));
  }, [filter.categories, setFilter, categoryOptions]);

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-slate-100 shadow-sm
                    flex-wrap text-xs flex-shrink-0">


      {/* 参考年（单年） */}
      <div className="flex items-center gap-2">
        <span className="text-slate-500">{t("filter.year")}</span>
        <SingleYearSlider
          minYear={minYear}
          maxYear={maxYear}
          value={currentYear}
          onChange={(v) => setFilter((f) => ({ ...f, refYear: v }))}
        />
        <span className="text-slate-700 font-mono">{currentYear}</span>
      </div>

      <div className="w-px h-4 bg-slate-200 mx-1" />

      {/* 大洲/小区过滤 */}
      <RegionSelect
        value={filter.regionFilter ?? null}
        onChange={(v) => setFilter((f) => ({ ...f, regionFilter: v }))}
      />

{/* 一带一路筛选 */}
<MultiSelect
        label={t("filter.bri")}
        options={["Yes", "No"]}
        selected={filter.briFilter}
        onChange={(v) => setFilter((f) => ({ ...f, briFilter: v }))}
        renderLabel={(v) => (v === "Yes" ? t("filter.briYes") : t("filter.briNo"))}
        allLabel={t("filter.allBri")}
      />

      {/* 国家发展状态筛选（三分类） */}
      <MultiSelect
        label={t("filter.ldc")}
        options={["LDC", "Developing", "Developed"]}
        selected={filter.ldcFilter}
        onChange={(v) => setFilter((f) => ({ ...f, ldcFilter: v }))}
        renderLabel={(v) => {
          if (v === "LDC")        return t("filter.ldcLDC");
          if (v === "Developing") return t("filter.ldcDeveloping");
          return t("filter.ldcDeveloped");
        }}
        allLabel={t("filter.allLdc")}
      />

      <div className="w-px h-4 bg-slate-200 mx-1" />
      
      {/* 能源类型 */}
      <MultiSelect
        label={t("filter.category")}
        options={categoryOptions}
        selected={filter.categories}
        onChange={(v) => setFilter((f) => ({ ...f, categories: v }))}
        renderLabel={(k) => CATEGORIES[k]?.en ?? k}
        allLabel={t("filter.allCategories")}
      />

      {/* 项目状态（4 大类） */}
      <MultiSelect
        label={t("filter.status")}
        options={VIZ_GROUP_KEYS}
        selected={filter.groups}
        onChange={(v) => setFilter((f) => ({ ...f, groups: v as VizGroup[] }))}
        renderLabel={(k) => VIZ_GROUPS[k]?.en ?? k}
        allLabel={t("filter.allStatuses")}
      />

      

      {/* 央企筛选 */}
      <div className="flex items-center gap-1.5">
        <span className="text-slate-500">{t("filter.soe")}</span>
        <select
          value={filter.soeFilter}
          onChange={(e) =>
            setFilter((f) => ({
              ...f,
              soeFilter: e.target.value as "all" | "soe" | "non_soe",
            }))
          }
          className="text-xs border border-slate-200 rounded-lg px-2 py-1 bg-white
                     text-slate-700 focus:outline-none focus:ring-1 focus:ring-brand-400"
        >
          <option value="all">{t("filter.soeAll")}</option>
          <option value="soe">{t("filter.soeYes")}</option>
          <option value="non_soe">{t("filter.soeNo")}</option>
        </select>
      </div>

      <div className="w-px h-4 bg-slate-200 mx-1" />

      {/* 着色方式 */}
      <div className="flex items-center gap-1.5">
        <span className="text-slate-500">{t("map.colorBy")}</span>
        <select
          value={colorBy}
          onChange={(e) => onColorByChange(e.target.value as ColorBy)}
          className="text-xs border border-slate-200 rounded-lg px-2 py-1 bg-white
                     text-slate-700 focus:outline-none focus:ring-1 focus:ring-brand-400"
        >
          <option value="count">{t("map.byCount")}</option>
          <option value="capacity">{t("map.byCapacity")}</option>
        </select>
      </div>

      {/* 重置 */}
      <button onClick={resetFilter} className="btn-ghost ml-auto text-xs">
        <RotateCcw size={12} />
        {t("filter.reset")}
      </button>
    </div>
  );
}
