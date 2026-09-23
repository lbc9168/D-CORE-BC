/**
 * domains.ts —— Map/Table/Overview 数据域配置的单一定义点。
 *
 * 两套互斥数据集（可相加、无重叠）：
 * - renewable：主表现状口径（PV/WD/HY/GT/BM/HB + ES 可见，ES 不进 GW 聚合）
 * - txmf     ：tx_flat/mf_flat 专表（度量仅 count——TX 输送容量覆盖率 2.7%、MF 单位异质）
 *
 * 储能不是第三个域，而是发电域的"度量透镜"：选中储能度量（MWh/MW）时地图按储能
 * 口径着色、列表聚焦 isStorageLens 子集。透镜谓词是数据驱动并集：
 * 储能类别 ∪ 储能状态 ∪ 技术标签 ∪ 储能数值；不再依赖综合能源旧“光储”子类。
 */

import type { DomainKey, MetricKey, Project } from "@/types";
import { CATEGORIES } from "@/utils/enums";

export interface DomainMeta {
  labelKey: string;      // i18n key：英文主名
  metrics: MetricKey[];  // 该域可用度量，首个为默认
}

export const DOMAIN_KEYS: DomainKey[] = ["renewable", "txmf"];

export const DOMAIN_META: Record<DomainKey, DomainMeta> = {
  // 度量只留三项（用户定：Power (MW) 太多余，储能透镜以 MWh 为唯一入口）
  renewable: {
    labelKey: "domain.renewable",
    metrics: ["count", "capacity_mw", "storage_energy_mwh"],
  },
  txmf: { labelKey: "domain.txmf", metrics: ["count"] },
};

/** 储能透镜度量：选中时列表聚焦 isStorageLens 子集 */
export function isStorageMetric(m: MetricKey): boolean {
  return m === "storage_energy_mwh" || m === "storage_power_mw";
}

export function defaultMetric(domain: DomainKey): MetricKey {
  return DOMAIN_META[domain].metrics[0];
}

export function isDomainKey(v: string | null | undefined): v is DomainKey {
  return v === "renewable" || v === "txmf";
}

// 类别中文键按 code 解析（DB 存中文键；code 是稳定标识，避免散落硬编码中文）
const keyByCode = (code: string): string =>
  Object.entries(CATEGORIES).find(([, m]) => m.code === code)?.[0] ?? "";

export const ES_CATEGORY_KEY = keyByCode("ES");
export const HB_CATEGORY_KEY = keyByCode("HB");
export const PV_CATEGORY_KEY = keyByCode("PV");
export const TX_CATEGORY_KEY = keyByCode("TX");
export const MF_CATEGORY_KEY = keyByCode("MF");

/** 储能透镜谓词：储能类别、状态、标签或容量任一显示存在储能即可命中。 */
export function isStorageLens(p: Project): boolean {
  if (p.category === ES_CATEGORY_KEY) return true;
  if (p.storage_status && p.storage_status !== "not_mentioned") return true;
  if (p.technology_tags?.some((tag) => tag.includes("储能") || tag.endsWith("储"))) return true;
  return p.storage_power_mw != null || p.storage_energy_mwh != null;
}
