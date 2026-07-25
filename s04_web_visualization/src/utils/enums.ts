// 前端枚举单一可信源加载器：读构建期拷贝的 enums.json。
import raw from "@/generated/enums.json";
import type { VizGroup } from "@/types";

type CategoryMeta = { zh: string; en: string; code: string; color: string; no_capacity: boolean; hidden: boolean; excluded: boolean };
type StageMeta = { zh: string; en: string; priority: number; color: string; hidden: boolean; viz_group: string | null };
type VizGroupMeta = { zh: string; en: string; color: string; order: number };
type SimpleMeta = { zh: string; en: string };

export const CATEGORIES = raw.category as Record<string, CategoryMeta>;
export const STAGES = raw.project_stage as Record<string, StageMeta>;
export const FINANCING_MODELS = raw.financing_model as Record<string, SimpleMeta>;
export const INVOLVEMENT_TYPES = raw.involvement_type as Record<string, SimpleMeta>;

export const isHiddenCategory = (zh: string) => CATEGORIES[zh]?.hidden ?? false;
export const isExcludedCategory = (zh: string) => CATEGORIES[zh]?.excluded ?? false;
export const isNoCapacityCategory = (zh: string) => CATEGORIES[zh]?.no_capacity ?? false;
export const isHiddenStage = (zh: string) => STAGES[zh]?.hidden ?? false;
export const stageColor = (zh: string) => STAGES[zh]?.color ?? "#9CA3AF";

export const VIZ_GROUPS = raw.viz_group as Record<string, VizGroupMeta>;

// 子类枚举（仅光伏/综合能源有；_default 为 NULL 时的导出层默认渲染值）
type SubcategoryDef = { _default: string; values: Record<string, SimpleMeta> };
export const SUBCATEGORIES = Object.fromEntries(
  Object.entries(raw.subcategory as Record<string, unknown>).filter(
    ([k, v]) => !k.startsWith("_") && typeof v === "object" && v !== null,
  ),
) as Record<string, SubcategoryDef>;

/** 某类别的子类枚举定义（无子类维度的类别返回 null） */
export const subcategoriesFor = (categoryZh: string): SubcategoryDef | null =>
  SUBCATEGORIES[categoryZh] ?? null;

/** 细 stage 中文键 → 4 大类 key（无则 null）。 */
export const stageToGroup = (stageZh: string): VizGroup | null =>
  (STAGES[stageZh]?.viz_group ?? null) as VizGroup | null;
