import { useState } from "react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";
import YearPills from "@/components/overview/YearPills";

/** Overview 信息仪表盘：年份胶囊驱动全页统计（与 FilterBar 解耦，消费 allProjects） */
export default function OverviewPage() {
  const { allProjects, availableYears, loading, error } = useData();
  const { t } = useLang();
  // null = 全部年份（默认）；选中某年后全页统计随之切换
  const [year, setYear] = useState<number | null>(null);

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

        {/* Task 3-5 在此填充：KPI 卡 / 说明条 / 状态构成 / Top 国家 / 趋势 + 企业图 */}
        <div className="card p-8 text-center text-slate-400 text-sm">
          {allProjects.length} projects loaded — dashboard sections coming in next tasks
        </div>
      </div>
    </div>
  );
}
