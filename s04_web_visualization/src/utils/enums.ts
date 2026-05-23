// 前端枚举单一可信源加载器：读构建期拷贝的 enums.json。
import raw from "@/generated/enums.json";

type CategoryMeta = { zh: string; en: string; code: string; color: string; no_capacity: boolean; hidden: boolean; excluded: boolean };
type StageMeta = { zh: string; en: string; priority: number; color: string; hidden: boolean };
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
