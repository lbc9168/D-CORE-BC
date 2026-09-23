import { X } from "lucide-react";
import type { TxMfProject } from "@/types";
import { CATEGORIES, VIZ_GROUPS } from "@/types";
import { stageToGroup } from "@/utils/enums";
import { useLang } from "@/contexts/LanguageContext";
import { formatCountryName, formatProductionCapacity } from "@/utils/display";
import { TX_CATEGORY_KEY as TX_KEY, MF_CATEGORY_KEY as MF_KEY } from "@/utils/domains";
import EntityList from "./EntityList";
import ExternalReferences from "./ExternalReferences";

interface TxMfProjectDetailProps {
  project: TxMfProject;
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

/**
 * TX/MF 专表精简详情：只渲染专表实际有的信息（关键规格/基本信息/参与实体）。
 * 专表没有时间线/融资/开发商字段；可选参考资料无有效条目时不渲染。
 */
export default function TxMfProjectDetail({ project: p, onClose }: TxMfProjectDetailProps) {
  const { t } = useLang();
  const cat = CATEGORIES[p.kind === "tx" ? TX_KEY : MF_KEY];
  const grp = stageToGroup(p.latest_stage);
  const grpMeta = grp ? VIZ_GROUPS[grp] : null;
  const stageColor = grpMeta?.color ?? "#94a3b8";

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

      {/* 标签行 */}
      <div className="flex flex-wrap gap-1.5 px-4 py-2 border-b border-slate-100">
        {cat && (
          <span className="badge text-white text-xs" style={{ backgroundColor: cat.color }}>
            {cat.en}
          </span>
        )}
        <span
          className="badge text-xs"
          style={{ backgroundColor: stageColor + "20", color: stageColor }}
        >
          {p.latest_stage_en || grpMeta?.en || p.latest_stage}
        </span>
        {p.bri_country === "Yes" && (
          <span className="badge bg-green-100 text-green-700 text-xs">BRI</span>
        )}
        {p.ldc === "Yes" && (
          <span className="badge bg-amber-100 text-amber-700 text-xs">LDC</span>
        )}
      </div>

      {/* 正文 */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {/* 关键规格：普通 Row 列表（低覆盖率字段不宜大数字条强调），有值才显示 */}
        {(p.kind === "tx"
          ? p.voltage_kv != null || p.line_length_km != null || p.transmission_capacity_mw != null
          : p.production_capacity_value != null) && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.keySpecs")}
            </h4>
            <dl className="bg-slate-50 rounded-xl px-3 py-1.5">
              {p.kind === "tx" ? (
                <>
                  <Row label={t("projectDetail.voltage")} value={p.voltage_kv} />
                  <Row label={t("projectDetail.lineLength")} value={p.line_length_km} />
                  <Row label={t("projectDetail.transmissionCapacity")} value={p.transmission_capacity_mw} />
                </>
              ) : (
                <Row
                  label={t("projectDetail.productionCapacity")}
                  value={formatProductionCapacity(p.production_capacity_value, p.production_capacity_unit)}
                />
              )}
            </dl>
          </section>
        )}

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
            <Row label={t("projectDetail.category")} value={cat?.en} />
            <Row label={t("projectList.year")} value={p.event_year} />
          </dl>
        </section>

        {/* 中方实体（共享 EntityList） */}
        {p.all_entities && p.all_entities.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.entities")}
            </h4>
            <EntityList entities={p.all_entities} />
          </section>
        )}
        <ExternalReferences references={p.external_references} />
      </div>
    </div>
  );
}
