import { useRef, useState, useEffect, useMemo } from "react";
import { RotateCcw, ChevronDown, Globe } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useDomain } from "@/contexts/DomainContext";
import { useLang } from "@/contexts/LanguageContext";
import { CATEGORIES, VIZ_GROUPS, VIZ_GROUP_KEYS } from "@/types";
import type { RegionFilter, VizGroup } from "@/types";
import { isHiddenCategory, SUBCATEGORIES } from "@/utils/enums";
import { CONTINENTS, CONTINENT_SUBREGIONS } from "@/utils/continentMapping";

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
                   bg-white border border-slate-200 rounded-full px-2.5 py-1.5
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
                   bg-white border border-slate-200 rounded-full px-2.5 py-1.5
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

/** 双柄年份范围滑块（[start, end]；状态/配色按 end 年计算）。 */
function YearRangeSlider({
  minYear,
  maxYear,
  value,
  onChange,
}: {
  minYear: number;
  maxYear: number;
  value: [number, number];
  onChange: (v: [number, number]) => void;
}) {
  const range = maxYear - minYear || 1;
  const leftPct = ((value[0] - minYear) / range) * 100;
  const rightPct = 100 - ((value[1] - minYear) / range) * 100;

  // 当下限 thumb 靠近右侧时，让它浮在上层以便可拖动
  const lowerZClass = value[0] >= value[1] ? "z-20" : "z-10";

  return (
    <div className="relative h-5 flex items-center" style={{ width: "96px" }}>
      {/* 背景轨道 */}
      <div className="absolute inset-x-0 h-1 bg-slate-200 rounded-full" />
      {/* 选中区间高亮 */}
      <div
        className="absolute h-1 bg-brand-400 rounded-full"
        style={{ left: `${leftPct}%`, right: `${rightPct}%` }}
      />
      {/* 下限滑块 */}
      <input
        type="range"
        min={minYear}
        max={maxYear}
        value={value[0]}
        className={`dual-range-input ${lowerZClass}`}
        onChange={(e) => {
          const v = Number(e.target.value);
          onChange([Math.min(v, value[1]), value[1]]);
        }}
      />
      {/* 上限滑块 */}
      <input
        type="range"
        min={minYear}
        max={maxYear}
        value={value[1]}
        className="dual-range-input z-10"
        onChange={(e) => {
          const v = Number(e.target.value);
          onChange([value[0], Math.max(v, value[0])]);
        }}
      />
    </div>
  );
}

interface FilterBarProps {
  onReset?: () => void;
}

/** 渲染跨页面筛选栏，并允许页面在统一重置时清理自己的局部状态。 */
export default function FilterBar({ onReset }: FilterBarProps = {}) {
  const { filter, setFilter, resetFilter, availableYears } = useData();
  const { domain } = useDomain();
  const { t } = useLang();

  const minYear = availableYears[0] ?? 2022;   // 数据集起始年兜底（availableYears 为空时）
  const maxYear = availableYears[availableYears.length - 1] ?? new Date().getFullYear();
  const currentRange = filter.yearRange ?? [minYear, maxYear];
  const categoryOptions = useMemo(
    () => Object.keys(CATEGORIES).filter((k) => !isHiddenCategory(k)),
    [],
  );

  // 清理旧筛选状态中已隐藏的类别
  useEffect(() => {
    if (filter.categories.every((cat) => categoryOptions.includes(cat))) return;
    setFilter((f) => ({ ...f, categories: f.categories.filter((cat) => categoryOptions.includes(cat)) }));
  }, [filter.categories, setFilter, categoryOptions]);

  // 子类二级筛选只承载项目形态：当前仅光伏集中式/分布式，不暴露综合能源旧组合子类。
  const subcatCats = useMemo(
    () => filter.categories.filter((c) => CATEGORIES[c]?.code === "PV" && SUBCATEGORIES[c]),
    [filter.categories],
  );
  const subcatOptions = useMemo(
    () => subcatCats.flatMap((c) => Object.keys(SUBCATEGORIES[c].values)),
    [subcatCats],
  );
  // 子类键 → 「类别 · 子类」前缀标签（两类同选时消歧）
  const subcatLabel = useMemo(() => {
    const m: Record<string, string> = {};
    subcatCats.forEach((c) => {
      Object.entries(SUBCATEGORIES[c].values).forEach(([k, v]) => {
        m[k] = `${CATEGORIES[c]?.en ?? c} · ${v.en}`;
      });
    });
    return m;
  }, [subcatCats]);

  // 类别取消选中时联动清空对应子类（防残留子类把列表筛空）
  useEffect(() => {
    if (filter.subcategories.every((s) => subcatOptions.includes(s))) return;
    setFilter((f) => ({ ...f, subcategories: f.subcategories.filter((s) => subcatOptions.includes(s)) }));
  }, [filter.subcategories, subcatOptions, setFilter]);

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-slate-100 shadow-sm
                    flex-wrap text-xs flex-shrink-0">


      {/* 年份范围（双柄） */}
      <div className="flex items-center gap-2">
        <span className="text-slate-500">{t("filter.yearRange")}</span>
        <span className="text-slate-700 font-mono">{currentRange[0]}</span>
        <YearRangeSlider
          minYear={minYear}
          maxYear={maxYear}
          value={currentRange}
          onChange={(v) => setFilter((f) => ({ ...f, yearRange: v }))}
        />
        <span className="text-slate-700 font-mono">{currentRange[1]}</span>
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
      
      {/* 能源类型（仅发电域；储能域构成固定、txmf 域用子域 Tab） */}
      {domain === "renewable" && (
        <MultiSelect
          label={t("filter.category")}
          options={categoryOptions}
          selected={filter.categories}
          onChange={(v) => {
            const allowedSubcategories = new Set(
              v
                .filter((category) => CATEGORIES[category]?.code === "PV" && SUBCATEGORIES[category])
                .flatMap((category) => Object.keys(SUBCATEGORIES[category].values)),
            );
            setFilter((f) => ({
              ...f,
              categories: v,
              subcategories: f.subcategories.filter((subcategory) => allowedSubcategories.has(subcategory)),
            }));
          }}
          renderLabel={(k) => CATEGORIES[k]?.en ?? k}
          allLabel={t("filter.allCategories")}
        />
      )}

      {/* 项目形态子类二级筛选（选中光伏时出现） */}
      {domain === "renewable" && subcatCats.length > 0 && (
        <MultiSelect
          label={t("filter.subtype")}
          options={subcatOptions}
          selected={filter.subcategories}
          onChange={(v) => setFilter((f) => ({ ...f, subcategories: v }))}
          renderLabel={(k) => subcatLabel[k] ?? k}
          allLabel={t("filter.allSubtypes")}
        />
      )}

      {/* TX/MF 子域 Tab（仅 txmf 域） */}
      {domain === "txmf" && (
        <div className="inline-flex border border-slate-200 rounded-lg overflow-hidden">
          {(["all", "tx", "mf"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setFilter((f) => ({ ...f, txmfKind: k }))}
              className={`px-2.5 py-1.5 text-xs border-r border-slate-200 last:border-r-0 transition-colors ${
                filter.txmfKind === k
                  ? "bg-brand-50 text-brand-600 font-semibold"
                  : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              {k === "all" ? t("filter.txmfAll") : k === "tx" ? t("filter.txmfTx") : t("filter.txmfMf")}
            </button>
          ))}
        </div>
      )}

      {/* 项目状态（4 大类；txmf 专表无时间线，不支持状态筛选） */}
      {domain !== "txmf" && (
        <MultiSelect
          label={t("filter.status")}
          options={VIZ_GROUP_KEYS}
          selected={filter.groups}
          onChange={(v) => setFilter((f) => ({ ...f, groups: v as VizGroup[] }))}
          renderLabel={(k) => VIZ_GROUPS[k]?.en ?? k}
          allLabel={t("filter.allStatuses")}
        />
      )}



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

      {/* 着色度量选择器已随 V3 视角条移入 DomainSwitcher（度量是域的属性，不是筛选条件） */}

      {/* 重置 */}
      <button
        onClick={() => {
          resetFilter();
          onReset?.();
        }}
        className="btn-ghost ml-auto text-xs"
      >
        <RotateCcw size={12} />
        {t("filter.reset")}
      </button>
    </div>
  );
}
