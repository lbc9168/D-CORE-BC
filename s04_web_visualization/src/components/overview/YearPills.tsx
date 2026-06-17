/** 年份胶囊选择器（cobalt 风格）：一排圆角胶囊，选中者 brand 实底；首位"全部" */
export default function YearPills({
  years,
  value,
  onChange,
  allLabel,
}: {
  years: number[];
  value: number | null; // null = 全部年份
  onChange: (v: number | null) => void;
  allLabel: string;
}) {
  const pill = (active: boolean) =>
    `px-3 py-1 rounded-full text-xs font-semibold transition-colors select-none ${
      active ? "bg-brand-600 text-white shadow-sm" : "text-slate-500 hover:bg-slate-100"
    }`;
  return (
    <div className="flex items-center gap-0.5 bg-white border border-slate-200 rounded-full p-1 shadow-sm">
      <button type="button" aria-pressed={value == null} className={pill(value == null)} onClick={() => onChange(null)}>
        {allLabel}
      </button>
      {years.map((y) => (
        <button type="button" key={y} aria-pressed={value === y} className={pill(value === y)} onClick={() => onChange(y)}>
          {y}
        </button>
      ))}
    </div>
  );
}
