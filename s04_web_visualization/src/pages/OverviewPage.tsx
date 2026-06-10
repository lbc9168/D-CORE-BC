import { useState, useMemo } from "react";
import { Layers, Zap, Globe2, Building2, Info } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import YearPills from "@/components/overview/YearPills";
import KpiCard from "@/components/overview/KpiCard";
import { activeProjectsAt, buildYearSeries, formatBig } from "@/utils/overviewStats";
import { hasCapacity } from "@/utils/capacityPolicy";

/** Overview 信息仪表盘：年份胶囊驱动全页统计（与 FilterBar 解耦，消费 allProjects） */
export default function OverviewPage() {
  const { allProjects, availableYears, loading, error } = useData();
  const { t } = useLang();
  // null = 全部年份（默认）；选中某年后全页统计随之切换
  const [year, setYear] = useState<number | null>(null);

  // 迷你趋势线序列：每个可用年份各算一行 KPI
  const yearSeries = useMemo(
    () => buildYearSeries(allProjects, availableYears),
    [allProjects, availableYears],
  );
  // 选中年（null=全部）下的活跃项目集合，喂当前 KPI 与下方各图
  const activeProjects = useMemo(
    () => activeProjectsAt(allProjects, year),
    [allProjects, year],
  );
  // 当前 KPI 值：对 activeProjects 直接统计（year=null 时即全集口径）
  const current = useMemo(() => {
    let capacityMw = 0;
    const countries = new Set<string>();
    const entities = new Set<string>();
    for (const p of activeProjects) {
      if (hasCapacity(p.category)) capacityMw += p.capacity_mw ?? 0;
      if (p.country_iso) countries.add(p.country_iso);
      if (p.primary_entity) entities.add(p.primary_entity);
    }
    return { active: activeProjects.length, capacityMw, countries: countries.size, entities: entities.size };
  }, [activeProjects]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-slate-400 text-sm">
        {t("common.loading")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center text-red-400 text-sm">
        {t("common.error")}
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[1400px] mx-auto w-full px-6 py-6 space-y-4">
        {/* 标题行：标题 + 年份胶囊 */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h1 className="text-xl font-bold text-slate-800">
            {t("overview.title")}{" "}
            <span className="text-brand-600 underline decoration-2 underline-offset-4">
              {year == null ? t("overview.allYears") : year}
            </span>
          </h1>
          <YearPills
            years={availableYears}
            value={year}
            onChange={setYear}
            allLabel={t("overview.allYears")}
          />
        </div>

        {/* KPI 大数字卡 ×4 */}
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          <KpiCard label={t("overview.kpiActive")} value={formatBig(current.active)}
            icon={Layers} color="#2F6562"
            series={yearSeries.map((s) => ({ year: s.year, v: s.active }))} />
          <KpiCard label={t("overview.kpiCapacity")} value={formatBig(current.capacityMw)}
            icon={Zap} color="#F59E0B"
            series={yearSeries.map((s) => ({ year: s.year, v: s.capacityMw }))} />
          <KpiCard label={t("overview.kpiCountries")} value={formatBig(current.countries)}
            icon={Globe2} color="#3B82F6"
            series={yearSeries.map((s) => ({ year: s.year, v: s.countries }))} />
          <KpiCard label={t("overview.kpiEntities")} value={formatBig(current.entities)}
            icon={Building2} color="#8B5CF6"
            series={yearSeries.map((s) => ({ year: s.year, v: s.entities }))} />
        </div>

        {/* 口径说明条（cobalt 风格浅蓝信息条） */}
        <div className="flex items-start gap-2.5 bg-sky-50 border border-sky-100 rounded-xl px-4 py-3">
          <Info size={15} className="text-sky-500 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-slate-600 leading-relaxed">{t("overview.banner")}</p>
        </div>

        {/* Task 4-5 在此填充：状态构成 / Top 国家 / 趋势 + 企业图 */}
      </div>
    </div>
  );
}
