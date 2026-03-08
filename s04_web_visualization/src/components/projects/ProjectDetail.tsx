import { X, ExternalLink } from "lucide-react";
import type { Project } from "@/types";
import { CATEGORIES, STAGE_COLORS } from "@/types";
import { useLang } from "@/contexts/LanguageContext";

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
  const { t, lang } = useLang();
  const cat = CATEGORIES[p.category];
  const stageColor = STAGE_COLORS[p.latest_stage] ?? "#94a3b8";

  const name = lang === "zh" ? p.project_name : (p.project_name_en || p.project_name);

  return (
    <div className="flex flex-col h-full bg-white">
      {/* 顶栏 */}
      <div className="flex items-start gap-2 p-3 border-b border-slate-100">
        <div className="flex-1">
          <div className="text-xs text-slate-400 mb-0.5">{p.project_id}</div>
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">{name}</h3>
          {lang === "en" && p.project_name && (
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
            {lang === "zh" ? cat.zh : cat.en}
          </span>
        )}
        <span
          className="badge text-xs"
          style={{ backgroundColor: stageColor + "20", color: stageColor }}
        >
          {p.latest_stage}
        </span>
        {p.capacity_mw && (
          <span className="badge bg-slate-100 text-slate-600 text-xs">
            {p.capacity_mw.toFixed(1)} MW
          </span>
        )}
        {p.bri_country === "TRUE" && (
          <span className="badge bg-green-100 text-green-700 text-xs">BRI</span>
        )}
        {p.ldc === "TRUE" && (
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
            <Row label={t("projectDetail.country")} value={p.country} />
            <Row
              label={t("projectDetail.province")}
              value={p.province ?? t("projectDetail.noProvince")}
            />
            <Row label={t("projectDetail.stageDate")} value={p.latest_stage_date || undefined} />
            <Row label={t("projectDetail.financing")} value={p.financing_model || undefined} />
            <Row label={t("projectDetail.eventCount")} value={p.event_count} />
            <Row
              label={t("projectDetail.verification")}
              value={
                p.verification_status === "verified"
                  ? t("projectDetail.verified")
                  : t("projectDetail.unverified")
              }
            />
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
                  <div className="font-medium text-slate-800">
                    {lang === "zh" ? ent.name : (ent.name_en || ent.name)}
                  </div>
                  {ent.subsidiary && (
                    <div className="text-slate-500 mt-0.5">
                      {lang === "zh" ? ent.subsidiary : (ent.subsidiary_en || ent.subsidiary)}
                    </div>
                  )}
                  {ent.involvement_type && (
                    <div className="text-slate-400 mt-0.5">{ent.involvement_type}</div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* 信息来源 */}
        {p.source_links && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.sources")}
            </h4>
            <div className="space-y-1">
              {p.source_links.split(",").map((url, i) => {
                const trimmed = url.trim();
                const isUrl = trimmed.startsWith("http");
                return isUrl ? (
                  <a
                    key={i}
                    href={trimmed}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-brand-600 hover:underline text-xs break-all"
                  >
                    <ExternalLink size={10} className="flex-shrink-0" />
                    {trimmed}
                  </a>
                ) : (
                  <p key={i} className="text-xs text-slate-600 break-words">{trimmed}</p>
                );
              })}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
