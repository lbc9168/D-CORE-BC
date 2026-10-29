import { useEffect } from "react";
import { Zap, Factory } from "lucide-react";
import type { DomainKey } from "@/types";
import { DOMAIN_KEYS, DOMAIN_META } from "@/utils/domains";
import { METRIC_META } from "@/utils/metrics";
import { useData } from "@/contexts/DataContext";
import { useDomain } from "@/contexts/DomainContext";
import { useLang } from "@/contexts/LanguageContext";
import { translations } from "@/i18n";

/**
 * DomainSwitcher —— 页顶"视角条"：两张互斥数据集卡片（发电 | 输变电·制造业），
 * 选中卡片内嵌该域度量选择器（储能 MWh/MW 是发电域的"透镜"度量，非独立数据集）。
 * showMetrics=false 供 Table/Overview 复用（这两页不消费地图度量）。
 */

const DOMAIN_ICONS: Record<DomainKey, typeof Zap> = {
  renewable: Zap,
  txmf: Factory,
};

export default function DomainSwitcher({ showMetrics = true }: { showMetrics?: boolean }) {
  const { t } = useLang();
  const { domain, setDomain, metric, setMetric } = useDomain();
  const { allProjects, txProjects, mfProjects, ensureTxMfLoaded } = useData();

  // 挂载即预取 tx/mf（431KB+142KB，只为角标数字；域数据消费仍是懒加载语义）
  useEffect(() => { ensureTxMfLoaded(); }, [ensureTxMfLoaded]);

  const txmfCount = txProjects.length + mfProjects.length;
  const counts: Record<DomainKey, string> = {
    renewable: allProjects.length.toLocaleString(),
    txmf: txmfCount > 0 ? txmfCount.toLocaleString() : "—",
  };

  return (
    <div className="flex items-stretch gap-2 px-4 py-2 bg-brand-50/70 border-b border-brand-100
                    flex-wrap flex-shrink-0">
      {DOMAIN_KEYS.map((key) => {
        const meta = DOMAIN_META[key];
        const Icon = DOMAIN_ICONS[key];
        const active = key === domain;
        // 中文副名固定取 zh 字典（站点 UI 为英文，卡片双语呈现是设计的一部分）
        const zhSub = (translations.zh.domain as Record<string, string>)[key];

        return (
          <div
            key={key}
            role="button"
            tabIndex={0}
            onClick={() => setDomain(key)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setDomain(key); }}
            className={`flex items-center gap-2 rounded-[10px] border px-3 py-1.5 cursor-pointer
                        select-none transition-all bg-white ${
                          active
                            ? "border-brand-500 shadow-[0_2px_8px_rgba(47,101,98,0.18)]"
                            : "border-slate-200 hover:border-brand-200"
                        }`}
          >
            <Icon size={17} className={active ? "text-brand-500" : "text-slate-400"} />
            <span className={`text-xs font-semibold whitespace-nowrap ${
              active ? "text-brand-600" : "text-slate-600"
            }`}>
              {t(meta.labelKey)}
            </span>
            <span className="text-[10px] text-slate-400 whitespace-nowrap">{zhSub}</span>
            <span className={`badge font-bold ${
              active ? "bg-brand-100 text-brand-700" : "bg-slate-100 text-slate-500"
            }`} style={{ fontSize: "11px" }}>
              {counts[key]}
            </span>

            {/* 选中卡片内嵌度量选择器（域内唯一度量时仅展示不可切换） */}
            {showMetrics && active && (
              <span
                className="flex items-center gap-1 ml-1 pl-2.5 border-l border-slate-200"
                onClick={(e) => e.stopPropagation()}
              >
                {meta.metrics.map((m) => (
                  <button
                    key={m}
                    onClick={() => setMetric(m)}
                    className={`text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap transition-colors ${
                      m === metric
                        ? "bg-brand-500 text-white font-semibold"
                        : "text-slate-500 hover:bg-slate-100"
                    }`}
                  >
                    {t(METRIC_META[m].shortLabelKey)}
                  </button>
                ))}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
