import { X } from "lucide-react";
import type { Project } from "@/types";
import { CATEGORIES, STAGES, STAGE_COLORS } from "@/types";
import { useLang } from "@/contexts/LanguageContext";
import { formatCountryName } from "@/utils/display";

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
      <div className="flex items-start gap-2 p-3 border-b border-slate-100">
        <div className="flex-1">
          <div className="text-xs text-slate-400 mb-0.5">{p.project_id}</div>
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">{name}</h3>
          {p.project_name && p.project_name_en && p.project_name !== p.project_name_en && (
            <div className="text-xs text-slate-400 mt-0.5">{p.project_name}</div>
          )}
        </div>
        <button onClick={onClose} className="btn-ghost p-1 -mr-1 flex-shrink-0">
          <X size={16} />
        </button>
      </div>

      {/* 标签 */}
      <div className="flex flex-wrap gap-1.5 px-3 py-2 border-b border-slate-100">
        {cat && (
          <span className="badge text-white text-xs" style={{ backgroundColor: cat.color }}>
            {cat.en}
          </span>
        )}
        <span
          className="badge text-xs"
          style={{ backgroundColor: stageColor + "20", color: stageColor }}
        >
          {p.latest_stage_en || STAGES[p.latest_stage]?.en || p.latest_stage}
        </span>
        {p.capacity_mw != null && (
          <span className="badge bg-slate-100 text-slate-600 text-xs">
            {p.capacity_mw.toFixed(1)} MW
          </span>
        )}
        {p.bri_country === "Yes" && (
          <span className="badge bg-green-100 text-green-700 text-xs">BRI</span>
        )}
        {p.ldc === "Yes" && (
          <span className="badge bg-amber-100 text-amber-700 text-xs">LDC</span>
        )}
      </div>

      {/* 正文 */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-4">
        {/* 基本信息 */}
        <section>
          <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
            {t("projectDetail.basicInfo")}
          </h4>
          <dl>
            <Row
              label={t("projectDetail.country")}
              value={formatCountryName(p.country, p.country_iso, "en")}
            />
            <Row
              label={t("projectDetail.category")}
              value={cat ? cat.en : (p.category_en || p.category)}
            />
            <Row
              label={t("projectDetail.capacity")}
              value={p.capacity_mw != null ? `${p.capacity_mw.toFixed(1)} MW` : undefined}
            />
            <Row
              label={t("projectDetail.province")}
              value={p.province ?? t("projectDetail.noProvince")}
            />
            <Row label={t("projectDetail.eventCount")} value={p.event_count} />
            {p.verification_status === "verified" && (
              <Row label={t("projectDetail.verification")} value={t("projectDetail.verified")} />
            )}
          </dl>
        </section>

        {/* 时间线 */}
        {p.timeline_notes && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.timeline")}
            </h4>
            <p className="text-xs text-slate-700 leading-relaxed">{p.timeline_notes}</p>
          </section>
        )}

        {/* 中方实体 */}
        {p.all_entities && p.all_entities.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.entities")}
            </h4>
            <div className="space-y-2">
              {p.all_entities.map((ent, i) => (
                <div key={i} className="bg-slate-50 rounded-lg p-2.5 text-xs">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium text-slate-800">
                      {ent.name_en || ent.name}
                    </span>
                    {ent.is_central_soe === 1 && (
                      <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold
                                       bg-amber-100 text-amber-700 leading-none">
                        Central SOE
                      </span>
                    )}
                  </div>
                  {ent.subsidiary && (
                    <div className="text-slate-500 mt-0.5">
                      {ent.subsidiary_en || ent.subsidiary}
                    </div>
                  )}
                  {ent.involvement_type && (
                    <div className="text-slate-400 mt-0.5">
                      {ent.involvement_type_en || ent.involvement_type}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* 融资信息 */}
        {(() => {
          const fmValue =
            p.financing_model_en && p.financing_model_en !== "Unknown"
              ? p.financing_model_en
              : p.financing_model && p.financing_model !== "Unknown"
              ? p.financing_model
              : null;
          if (!fmValue && p.total_investment_usd == null) return null;
          return (
            <section>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                {t("projectDetail.financingSection")}
              </h4>
              <div className="bg-slate-50 rounded-lg p-2.5 text-xs space-y-1.5">
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
            <div className="bg-slate-50 rounded-lg p-2.5 text-xs space-y-1.5">
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
