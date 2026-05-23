/**
 * capacityPolicy.ts
 *
 * 储能（ES）和输变电建设（TX）项目的容量单位不是 MW，
 * 在任何"装机容量"聚合统计中应当排除，但项目数量统计仍计入。
 */

import { isNoCapacityCategory } from "@/utils/enums";

/** 判断某个项目是否应计入装机容量统计 */
export function hasCapacity(category: string | null | undefined): boolean {
  if (!category) return false;
  return !isNoCapacityCategory(category);
}
