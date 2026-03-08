import { RotateCcw, ChevronDown } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import { CATEGORIES, STAGES } from "@/types";
import type { ColorBy } from "@/types";

interface FilterBarProps {
  colorBy: ColorBy;
  onColorByChange: (v: ColorBy) => void;
}

/** 可折叠多选下拉（轻量实现，无第三方库） */
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
  const toggle = (v: string) =>
    selected.includes(v) ? onChange(selected.filter((x) => x !== v)) : onChange([...selected, v]);

  return (
    <div className="group relative">
      <button className="flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900
                         bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 gap-1.5
                         transition-colors hover:border-brand-300">
        <span>{label}</span>
        {selected.length > 0 && (
          <span className="badge bg-brand-100 text-brand-700">{selected.length}</span>
        )}
        <ChevronDown size={12} className="text-slate-400" />
      </button>
      {/* 下拉面板（hover 显示）*/}
      <div className="absolute top-full left-0 mt-1 z-40 hidden group-hover:block
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
          <label key={opt}
            className="flex items-center gap-2 px-3 py-1.5 text-xs text-slate-700
                       hover:bg-slate-50 cursor-pointer">
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
    </div>
  );
}

export default function FilterBar({ colorBy, onColorByChange }: FilterBarProps) {
  const { filter, setFilter, resetFilter, filteredProjects, availableYears, availableEntities } =
    useData();
  const { t, lang } = useLang();

  const minYear = availableYears[0] ?? 2000;
  const maxYear = availableYears[availableYears.length - 1] ?? new Date().getFullYear();
  const currentRange = filter.yearRange ?? [minYear, maxYear];

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-slate-100
                    flex-wrap text-xs flex-shrink-0">
      {/* 项目计数 */}
      <span className="text-slate-500 font-medium mr-1">
        {t("filter.projectsCount", { count: filteredProjects.length })}
      </span>

      <div className="w-px h-4 bg-slate-200 mx-1" />

      {/* 年份滑块 */}
      <div className="flex items-center gap-2">
        <span className="text-slate-500">{t("filter.yearRange")}</span>
        <input
          type="range"
          min={minYear}
          max={maxYear}
          value={currentRange[0]}
          className="w-20 accent-brand-600"
          onChange={(e) =>
            setFilter((f) => ({
              ...f,
              yearRange: [Number(e.target.value), Math.max(Number(e.target.value), currentRange[1])],
            }))
          }
        />
        <span className="text-slate-700 font-mono w-10 text-center">{currentRange[0]}</span>
        <span className="text-slate-400">–</span>
        <input
          type="range"
          min={minYear}
          max={maxYear}
          value={currentRange[1]}
          className="w-20 accent-brand-600"
          onChange={(e) =>
            setFilter((f) => ({
              ...f,
              yearRange: [Math.min(currentRange[0], Number(e.target.value)), Number(e.target.value)],
            }))
          }
        />
        <span className="text-slate-700 font-mono w-10 text-center">{currentRange[1]}</span>
      </div>

      <div className="w-px h-4 bg-slate-200 mx-1" />

      {/* 能源类型 */}
      <MultiSelect
        label={t("filter.category")}
        options={Object.keys(CATEGORIES)}
        selected={filter.categories}
        onChange={(v) => setFilter((f) => ({ ...f, categories: v }))}
        renderLabel={(k) => CATEGORIES[k]?.[lang] ?? k}
        allLabel={t("filter.allCategories")}
      />

      {/* 项目阶段 */}
      <MultiSelect
        label={t("filter.stage")}
        options={Object.keys(STAGES)}
        selected={filter.stages}
        onChange={(v) => setFilter((f) => ({ ...f, stages: v }))}
        renderLabel={(k) => STAGES[k]?.[lang] ?? k}
        allLabel={t("filter.allStages")}
      />

      {/* 中方企业 */}
      <MultiSelect
        label={t("filter.entity")}
        options={availableEntities.slice(0, 50)}
        selected={filter.entities}
        onChange={(v) => setFilter((f) => ({ ...f, entities: v }))}
        allLabel={t("filter.allEntities")}
      />

      {/* BRI 筛选 */}
      <label className="flex items-center gap-1.5 cursor-pointer text-slate-600 hover:text-slate-900">
        <input
          type="checkbox"
          checked={filter.briOnly}
          onChange={(e) => setFilter((f) => ({ ...f, briOnly: e.target.checked }))}
          className="accent-brand-600"
        />
        {t("filter.briOnly")}
      </label>

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
