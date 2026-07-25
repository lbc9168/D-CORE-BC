import { X } from "lucide-react";
import type { Project } from "@/types";
import { CATEGORIES, STAGES, STAGE_COLORS } from "@/types";
import { useLang } from "@/contexts/LanguageContext";
import { formatCountryName } from "@/utils/display";
import EntityList from "./EntityList";

interface ProjectDetailProps {
  project: Project;
  onClose: () => void;
}

function Row({ label, value }: { label: string; value?: string | number | null }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex gap-2 py-1.5 border-b border-slate-50 last:border-0">
      <dt className="text-slate-400 text-xs w-24 flex-shrink-0 pt-0.5">{label}</dt>
      <dd className="text-slate-800 text-xs flex-1 break-words">{value}</dd>
    </div>
  );
}

export default function ProjectDetail({ project: p, onClose }: ProjectDetailProps) {
  const { t } = useLang();
  const cat = CATEGORIES[p.category];
  const stageColor = STAGE_COLORS[p.latest_stage] ?? "#94a3b8";

  const name = p.project_name_en || p.project_name;

  return (
    <div className="flex flex-col h-full bg-white">
      {/* 顶栏 */}
      <div className="flex items-start gap-2 p-4 border-b border-slate-100">
        <div className="flex-1">
          <div className="text-xs text-slate-400 mb-0.5">{p.project_id}</div>
          <h3 className="text-base font-semibold text-slate-800 leading-snug">{name}</h3>
          {p.project_name && p.project_name_en && p.project_name !== p.project_name_en && (
            <div className="text-xs text-slate-400 mt-0.5">{p.project_name}</div>
          )}
        </div>
        <button onClick={onClose} className="btn-ghost p-1 -mr-1 flex-shrink-0">
          <X size={16} />
        </button>
      </div>

      {/* 标签（capacity 徽章已移除——数字条中有更醒目的展示） */}
      <div className="flex flex-wrap gap-1.5 px-4 py-2 border-b border-slate-100">
        {cat && (
          <span className="badge text-white text-xs" style={{ backgroundColor: cat.color }}>
            {cat.en}
          </span>
        )}
        {p.subcategory_en && (
          <span
            className="badge text-xs border bg-white"
            style={{ color: cat?.color ?? "#64748B", borderColor: (cat?.color ?? "#64748B") + "66" }}
          >
            {p.subcategory_en}
          </span>
        )}
        <span
          className="badge text-xs"
          style={{ backgroundColor: stageColor + "20", color: stageColor }}
        >
          {p.latest_stage_en || STAGES[p.latest_stage]?.en || p.latest_stage}
        </span>
        {p.bri_country === "Yes" && (
          <span className="badge bg-green-100 text-green-700 text-xs">BRI</span>
        )}
        {p.ldc === "Yes" && (
          <span className="badge bg-amber-100 text-amber-700 text-xs">LDC</span>
        )}
      </div>

      {/* 关键数字条（cobalt 风格：彩色竖线 + 大数字）；event_count 恒有值，故无条件渲染 */}
      {/* 储能/光储字段有值才出现对应格：ES 项目 capacity 恒空，由储能双指标顶上（W30b） */}
      <div className="flex flex-wrap gap-x-6 gap-y-2 px-4 py-3 border-b border-slate-100 bg-slate-50/60">
        {p.capacity_mw != null && (
          <div className="border-l-[3px] pl-2.5" style={{ borderColor: cat?.color ?? "#2F6562" }}>
            <div className="text-lg font-bold text-slate-800 leading-tight">
              {p.capacity_mw.toFixed(1)}
              <span className="text-xs font-medium text-slate-400 ml-1">MW</span>
            </div>
            <div className="text-[11px] text-slate-400">{t("projectDetail.capacity")}</div>
          </div>
        )}
        {p.pv_capacity_mw != null && (
          <div className="border-l-[3px] border-amber-500 pl-2.5">
            <div className="text-lg font-bold text-slate-800 leading-tight">
              {p.pv_capacity_mw.toFixed(1)}
              <span className="text-xs font-medium text-slate-400 ml-1">MW</span>
            </div>
            <div className="text-[11px] text-slate-400">{t("projectDetail.pvSubCapacity")}</div>
          </div>
        )}
        {p.storage_power_mw != null && (
          <div className="border-l-[3px] border-violet-500 pl-2.5">
            <div className="text-lg font-bold text-slate-800 leading-tight">
              {p.storage_power_mw.toFixed(1)}
              <span className="text-xs font-medium text-slate-400 ml-1">MW</span>
            </div>
            <div className="text-[11px] text-slate-400">{t("projectDetail.storagePower")}</div>
          </div>
        )}
        {p.storage_energy_mwh != null && (
          <div className="border-l-[3px] border-violet-400 pl-2.5">
            <div className="text-lg font-bold text-slate-800 leading-tight">
              {p.storage_energy_mwh.toFixed(1)}
              <span className="text-xs font-medium text-slate-400 ml-1">MWh</span>
            </div>
            <div className="text-[11px] text-slate-400">{t("projectDetail.storageEnergy")}</div>
          </div>
        )}
        {p.total_investment_usd != null && (
          <div className="border-l-[3px] border-amber-400 pl-2.5">
            <div className="text-lg font-bold text-slate-800 leading-tight">
              {p.total_investment_usd.toFixed(1)}
              <span className="text-xs font-medium text-slate-400 ml-1">M USD</span>
            </div>
            <div className="text-[11px] text-slate-400">{t("projectDetail.investment")}</div>
          </div>
        )}
        <div className="border-l-[3px] border-slate-300 pl-2.5">
          <div className="text-lg font-bold text-slate-800 leading-tight">{p.event_count}</div>
          <div className="text-[11px] text-slate-400">{t("projectDetail.eventCount")}</div>
        </div>
      </div>

      {/* 正文 */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {/* 基本信息 */}
        <section>
          <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
            {t("projectDetail.basicInfo")}
          </h4>
          <dl className="bg-slate-50 rounded-xl px-3 py-1.5">
            <Row
              label={t("projectDetail.country")}
              value={formatCountryName(p.country, p.country_iso, "en")}
            />
            <Row
              label={t("projectDetail.province")}
              value={p.province ?? t("projectDetail.noProvince")}
            />
            <Row
              label={t("projectDetail.category")}
              value={cat ? cat.en : (p.category_en || p.category)}
            />
            <Row label={t("projectDetail.subtype")} value={p.subcategory_en} />
            {p.verification_status === "verified" && (
              <Row label={t("projectDetail.verification")} value={t("projectDetail.verified")} />
            )}
          </dl>
        </section>

        {/* 时间线：多事件用 timeline_notes（到月）；单事件回退显示该里程碑与月份 */}
        {(p.timeline_notes || p.event_month) && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.timeline")}
            </h4>
            <p className="bg-slate-50 rounded-xl px-3 py-2.5 text-xs text-slate-700 leading-relaxed">
              {p.timeline_notes
                ? p.timeline_notes
                : `${p.latest_stage_en || STAGES[p.latest_stage]?.en || p.latest_stage}: ${p.event_month}`}
            </p>
          </section>
        )}

        {/* 中方实体 */}
        {p.all_entities && p.all_entities.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.entities")}
            </h4>
            <EntityList entities={p.all_entities} />
          </section>
        )}

        {/* 项目开发商（逻辑同 Financing：有值才显示；unknown 不显示）*/}
        {(() => {
          const developerRaw = (p.project_developer_en || "").trim();
          const isUnknown =
            !developerRaw ||
            ["unknown", "未知", "未披露", "n/a", "na", "-", "—"].includes(developerRaw.toLowerCase());
          if (isUnknown) return null;
          return (
            <section>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                {t("projectDetail.developerSection")}
              </h4>
              <div className="bg-slate-50 rounded-xl p-3 text-xs">
                <div className="flex gap-2">
                  <span className="text-slate-400 w-24 flex-shrink-0">{t("projectDetail.developer")}</span>
                  <span className="text-slate-800 break-words">{developerRaw}</span>
                </div>
              </div>
            </section>
          );
        })()}

        {/* 融资信息 */}
        {(() => {
          const fmValue =
            p.financing_model_en && p.financing_model_en !== "Unknown"
              ? p.financing_model_en
              : p.financing_model && p.financing_model !== "Unknown"
              ? p.financing_model
              : null;
          // 区块出现条件覆盖全部四个字段，防"只有金额有值"时整块漏渲染（W30b）
          if (
            !fmValue &&
            p.total_investment_usd == null &&
            p.contract_amount_usd == null &&
            p.financing_amount_usd == null
          ) return null;
          return (
            <section>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                {t("projectDetail.financingSection")}
              </h4>
              <div className="bg-slate-50 rounded-xl p-3 text-xs space-y-1.5">
                {fmValue && (
                  <div className="flex gap-2">
                    <span className="text-slate-400 w-24 flex-shrink-0">{t("projectDetail.financing")}</span>
                    <span className="text-slate-800">{fmValue}</span>
                  </div>
                )}
                {p.total_investment_usd != null && (
                  <div className="flex gap-2">
                    <span className="text-slate-400 w-24 flex-shrink-0">{t("projectDetail.investment")}</span>
                    <span className="text-slate-800">{p.total_investment_usd.toFixed(1)}</span>
                  </div>
                )}
                {/* 合同金额/融资额度：2026-07 起新数据才有值，历史全空 → 条件渲染（用户拍板） */}
                {p.contract_amount_usd != null && (
                  <div className="flex gap-2">
                    <span className="text-slate-400 w-24 flex-shrink-0">{t("projectDetail.contractAmount")}</span>
                    <span className="text-slate-800">{p.contract_amount_usd.toFixed(1)}</span>
                  </div>
                )}
                {p.financing_amount_usd != null && (
                  <div className="flex gap-2">
                    <span className="text-slate-400 w-24 flex-shrink-0">{t("projectDetail.financingAmount")}</span>
                    <span className="text-slate-800">{p.financing_amount_usd.toFixed(1)}</span>
                  </div>
                )}
              </div>
            </section>
          );
        })()}

        {/* 外部链接 */}
        {((p.official_website_links?.length ?? 0) > 0 ||
          (p.non_official_website_links?.length ?? 0) > 0) && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.externalLinks")}
            </h4>
            <div className="bg-slate-50 rounded-xl p-3 text-xs space-y-1.5">
              {[...(p.official_website_links ?? []), ...(p.non_official_website_links ?? [])].map((url, i) => (
                <div key={i}>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-teal-700 hover:underline break-all"
                  >
                    {url}
                  </a>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* 信息来源（暂时隐藏，待 Bug 2 source_links JSON.parse 修复后重新启用） */}
      </div>
    </div>
  );
}
