import { useState, useMemo } from "react";
import { Layers, Zap, Globe2, Building2, Info, BatteryCharging, Cable, Factory } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useDomain } from "@/contexts/DomainContext";
import { useLang } from "@/contexts/LanguageContext";
import DomainSwitcher from "@/components/filters/DomainSwitcher";
import YearPills from "@/components/overview/YearPills";
import KpiCard from "@/components/overview/KpiCard";
import StatusDonut from "@/components/overview/StatusDonut";
import TopCountriesBar from "@/components/overview/TopCountriesBar";
import KindToggle, { type TxMfKind } from "@/components/overview/KindToggle";
import TimelineChart from "@/components/charts/TimelineChart";
import EntityChart from "@/components/charts/EntityChart";
import TxMfEntityChart from "@/components/charts/TxMfEntityChart";
import { activeProjectsAt, formatBig } from "@/utils/overviewStats";
import { txmfAtYear, txmfKpis, txmfStatusMix, filterTxMfKind } from "@/utils/txmfOverviewStats";
import { hasCapacity } from "@/utils/capacityPolicy";
import { isStorageLens } from "@/utils/domains";

/** Overview 信息仪表盘：视角条切换 发电/产业链 两套统计；年份胶囊驱动全页（与 FilterBar 解耦） */
export default function OverviewPage() {
  const { allProjects, txProjects, mfProjects, availableYears, loading, error } = useData();
  const { domain } = useDomain();
  const { t } = useLang();
  const isTxMf = domain === "txmf";
  // null = 全部年份（默认）；选中某年后全页统计随之切换
  const [year, setYear] = useState<number | null>(null);

  // ── 发电侧统计 ──
  const activeProjects = useMemo(
    () => activeProjectsAt(allProjects, year),
    [allProjects, year],
  );
  const current = useMemo(() => {
    let capacityMw = 0;
    let storageProjects = 0;
    let storageMwh = 0;
    const countries = new Set<string>();
    const entities = new Set<string>();
    for (const p of activeProjects) {
      if (hasCapacity(p.category)) capacityMw += p.capacity_mw ?? 0;
      if (isStorageLens(p)) {
        storageProjects++;
        storageMwh += p.storage_energy_mwh ?? 0;
      }
      if (p.country_iso) countries.add(p.country_iso);
      if (p.primary_entity) entities.add(p.primary_entity);
    }
    return {
      active: activeProjects.length,
      capacityMw,
      storageProjects,
      storageMwh,
      countries: countries.size,
      entities: entities.size,
    };
  }, [activeProjects]);

  // ── 产业链侧统计（count 口径；年份=终态事件年；donut/Top国家 各带独立 TX/MF 图表级筛选） ──
  const txmfAll = useMemo(() => [...txProjects, ...mfProjects], [txProjects, mfProjects]);
  const txmfRows = useMemo(() => txmfAtYear(txmfAll, year), [txmfAll, year]);
  const txmfStats = useMemo(() => txmfKpis(txmfRows), [txmfRows]);
  const [donutKind, setDonutKind] = useState<TxMfKind>("all");
  const [countryKind, setCountryKind] = useState<TxMfKind>("all");
  const txmfMix = useMemo(
    () => txmfStatusMix(filterTxMfKind(txmfRows, donutKind)),
    [txmfRows, donutKind],
  );
  const txmfCountryRows = useMemo(
    () => filterTxMfKind(txmfRows, countryKind),
    [txmfRows, countryKind],
  );

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
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* 视角条（发电 / 产业链；Overview 不消费地图度量，隐藏度量胶囊） */}
      <DomainSwitcher showMetrics={false} />

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-[1400px] mx-auto w-full px-6 py-6 space-y-4">
          {/* 标题行：标题 + 年份胶囊 */}
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <h1 className="text-2xl font-semibold text-slate-800">
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

          {!isTxMf ? (
            <>
              {/* KPI 大数字卡 ×5（含储能透镜汇总） */}
              <div className="grid grid-cols-2 xl:grid-cols-5 gap-4">
                <KpiCard label={t("overview.kpiActive")} value={formatBig(current.active)}
                  icon={Layers} color="#2F6562" />
                <KpiCard label={t("overview.kpiCapacity")}
                  value={(current.capacityMw / 1000).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  icon={Zap} color="#F59E0B" />
                <KpiCard
                  label={t("overview.kpiStorage", { gwh: (current.storageMwh / 1000).toFixed(1) })}
                  value={formatBig(current.storageProjects)}
                  icon={BatteryCharging} color="#8B5CF6" />
                <KpiCard label={t("overview.kpiCountries")} value={formatBig(current.countries)}
                  icon={Globe2} color="#3B82F6" />
                <KpiCard label={t("overview.kpiEntities")} value={formatBig(current.entities)}
                  icon={Building2} color="#64748B" />
              </div>

              {/* 口径说明条（cobalt 风格浅蓝信息条） */}
              <div className="flex items-start gap-2.5 bg-sky-50 border border-sky-100 rounded-xl px-4 py-3">
                <Info size={15} className="text-sky-500 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-slate-600 leading-relaxed">{t("overview.banner")}</p>
              </div>

              {/* 状态构成 + Top 国家 */}
              <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                <StatusDonut
                  projects={activeProjects}
                  year={year}
                  title={t("overview.statusMix")}
                  shareLabel={t("overview.share")}
                />
                <TopCountriesBar projects={activeProjects} metric="count"
                  title={t("overview.topByCount")} color="#2F6562" />
                <TopCountriesBar projects={activeProjects} metric="capacity"
                  title={t("overview.topByCapacity")} color="#F59E0B" />
              </div>

              {/* 趋势 + 企业排名 */}
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <div className="card p-4 min-h-[20rem] flex flex-col min-w-0">
                  <TimelineChart projects={allProjects} year={year} groups={[]} />
                </div>
                <div className="card p-4 min-h-[20rem] flex flex-col min-w-0">
                  <EntityChart projects={activeProjects} />
                </div>
              </div>
            </>
          ) : (
            <>
              {/* 产业链侧：全部 count 口径（专表无装机/时间线区间语义）；KPI 不放总数（TX+MF 即总数） */}
              <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
                <KpiCard label={t("overview.kpiTx")} value={formatBig(txmfStats.tx)}
                  icon={Cable} color="#F97316" />
                <KpiCard label={t("overview.kpiMf")} value={formatBig(txmfStats.mf)}
                  icon={Factory} color="#A78BFA" />
                <KpiCard label={t("overview.kpiCountries")} value={formatBig(txmfStats.countries)}
                  icon={Globe2} color="#3B82F6" />
                <KpiCard label={t("overview.kpiEntities")} value={formatBig(txmfStats.entities)}
                  icon={Building2} color="#2F6562" />
              </div>

              <div className="flex items-start gap-2.5 bg-sky-50 border border-sky-100 rounded-xl px-4 py-3">
                <Info size={15} className="text-sky-500 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-slate-600 leading-relaxed">{t("overview.bannerTxmf")}</p>
              </div>

              {/* 三张图各带独立 TX/MF 筛选（图表级，互不影响） */}
              <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                <StatusDonut
                  projects={[]}
                  year={null}
                  mix={txmfMix}
                  title={t("overview.statusMix")}
                  shareLabel={t("overview.share")}
                  headerExtra={<KindToggle value={donutKind} onChange={setDonutKind} />}
                />
                <TopCountriesBar projects={txmfCountryRows} metric="count"
                  title={t("overview.topByCount")} color="#2F6562"
                  headerExtra={<KindToggle value={countryKind} onChange={setCountryKind} />} />
                <div className="card p-4 min-h-[18rem] flex flex-col min-w-0">
                  <TxMfEntityChart rows={txmfRows} />
                </div>
              </div>
              {/* 趋势图不展示：专表只有终态事件年，无活动区间语义，按年趋势会误读 */}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
