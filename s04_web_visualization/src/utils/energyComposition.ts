/** energyComposition.ts —— 将结构化发电源、储能状态与技术标签整理成项目详情可展示的能源组合。 */

import type { Project } from "@/types";
import { CATEGORIES, GENERATION_SOURCES, STORAGE_STATUSES, TECHNOLOGY_TAGS } from "@/utils/enums";
import { ES_CATEGORY_KEY, HB_CATEGORY_KEY } from "@/utils/domains";

/** 判断项目是否有足够证据把储能作为能源组合的一部分展示。 */
export function hasStorageComponent(project: Project): boolean {
  if (project.category === ES_CATEGORY_KEY) return true;
  if (project.storage_power_mw != null || project.storage_energy_mwh != null) return true;
  if (project.storage_status === "confirmed") return true;
  return (project.technology_tags ?? []).some((tag) => tag.includes("储能") || tag.endsWith("储"));
}

/** 返回项目的英文能源组合；缺少结构化事实时不从项目名称推断。 */
export function formatEnergyMix(project: Project): string | null {
  const sourceLabels = Array.from(new Set(
    (project.generation_sources ?? []).map((source) => GENERATION_SOURCES[source]?.en ?? source),
  ));

  if (sourceLabels.length === 0 && project.category !== HB_CATEGORY_KEY && project.category !== ES_CATEGORY_KEY) {
    const categoryLabel = CATEGORIES[project.category]?.en ?? project.category_en;
    if (categoryLabel) sourceLabels.push(categoryLabel);
  }

  if (sourceLabels.length > 0) {
    if (hasStorageComponent(project)) sourceLabels.push("Energy Storage");
    return Array.from(new Set(sourceLabels)).join(" + ");
  }

  const technologyLabels = (project.technology_tags_en?.length
    ? project.technology_tags_en
    : (project.technology_tags ?? []).map((tag) => TECHNOLOGY_TAGS[tag]?.en ?? tag)
  ).filter(Boolean);
  if (technologyLabels.length > 0) return Array.from(new Set(technologyLabels)).join(" · ");
  if (project.category === ES_CATEGORY_KEY) return "Energy Storage";
  return null;
}

/** 返回可显示的英文储能状态，未提及储能时返回空。 */
export function formatStorageStatus(project: Project): string | null {
  if (!project.storage_status || project.storage_status === "not_mentioned") return null;
  return STORAGE_STATUSES[project.storage_status]?.en ?? project.storage_status;
}
