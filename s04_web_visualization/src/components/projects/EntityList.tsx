import type { Entity } from "@/types";
import { INVOLVEMENT_TYPES } from "@/utils/enums";

/** 中方参与实体卡片列表（ProjectDetail 与 TxMfProjectDetail 共用，样式保持一致） */
export default function EntityList({ entities }: { entities: Entity[] }) {
  return (
    <div className="space-y-2">
      {entities.map((ent, i) => {
        const groupLabel = ent.name_en || ent.name;
        const entityLabel =
          ent.entity_name_en || ent.entity_name || ent.subsidiary_en || ent.subsidiary;
        const showEntity = Boolean(entityLabel && entityLabel !== groupLabel);
        return (
          <div key={i} className="bg-slate-50 rounded-xl p-3 text-xs">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="font-medium text-slate-800">{groupLabel}</span>
              {ent.is_central_soe === 1 && (
                <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold
                                 bg-amber-100 text-amber-700 leading-none">
                  Central SOE
                </span>
              )}
              {ent.level != null && ent.level > 1 && (
                <span className="text-[10px] text-slate-400 leading-none">L{ent.level}</span>
              )}
            </div>
            {showEntity && (
              <div className="text-slate-600 mt-0.5">{entityLabel}</div>
            )}
            {ent.path && ent.path.length > 2 && (
              <div className="text-slate-400 mt-0.5">{ent.path.join(" › ")}</div>
            )}
            {ent.involvement_type && (
              <div className="text-slate-400 mt-0.5">
                {ent.involvement_type_en || INVOLVEMENT_TYPES[ent.involvement_type]?.en || ent.involvement_type}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
