/**
 * metrics.ts —— 地图/排名着色度量的单一定义点。
 *
 * 每个 MetricKey 对应 {i18n 标签 key, 单位, 取值函数}；WorldMap / ProvinceMap /
 * useCountryStats 全部查这张表，新增度量只需加一行，不再改各组件内部逻辑。
 * capacity_mw 度量保留 hasCapacity() 守卫：ES/TX/MF 不进装机聚合（capacityPolicy 语义不变）。
 */

import type { MetricKey } from "@/types";
import { hasCapacity } from "@/utils/capacityPolicy";

/** 度量取值的最小结构：Project 全兼容；TxMfProject 缺容量字段，除 count 外自然返回 null */
export interface MetricRow {
  category?: string;
  capacity_mw?: number | null;
  storage_power_mw?: number | null;
  storage_energy_mwh?: number | null;
}

export interface MetricMeta {
  labelKey: string;      // i18n key（图例标题）
  shortLabelKey: string; // i18n key（视角卡片内度量胶囊的短文案）
  unit: string;          // "MW" | "MWh" | ""（count 无单位）
}

export const METRIC_META: Record<MetricKey, MetricMeta> = {
  count: { labelKey: "map.byCount", shortLabelKey: "metric.shortCount", unit: "" },
  capacity_mw: { labelKey: "map.byCapacity", shortLabelKey: "metric.shortCapacityMw", unit: "MW" },
  storage_power_mw: { labelKey: "metric.storagePowerMw", shortLabelKey: "metric.shortStoragePowerMw", unit: "MW" },
  storage_energy_mwh: { labelKey: "metric.storageEnergyMwh", shortLabelKey: "metric.shortStorageEnergyMwh", unit: "MWh" },
};

/** 按度量取行的聚合值；null = 该行不计入该度量 */
export function metricValue(metric: MetricKey, row: MetricRow): number | null {
  switch (metric) {
    case "count":
      return 1;
    case "capacity_mw":
      return hasCapacity(row.category) ? row.capacity_mw ?? null : null;
    case "storage_power_mw":
      return row.storage_power_mw ?? null;
    case "storage_energy_mwh":
      return row.storage_energy_mwh ?? null;
  }
}
