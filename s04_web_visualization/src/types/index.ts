// ── 项目数据 ──────────────────────────────────────────────────────────────────

export interface Entity {
  name: string;
  name_en: string;
  subsidiary: string;
  subsidiary_en: string;
  involvement_type: string;
}

export interface Project {
  project_id: string;
  project_name: string;
  project_name_en: string;
  name_en_quality: string;
  country: string;
  country_iso: string;      // ISO 3166-1 alpha-3
  bri_country: string;
  ldc: string;
  location: string;
  province: string | null;
  category: string;         // PV / WD / HY / GT / BM / ES / HB
  capacity_mw: number | null;
  latest_stage: string;
  latest_stage_date: string;
  event_year: number | null;
  timeline_notes: string;
  event_count: number;
  financing_model: string;
  primary_entity: string;
  primary_entity_en: string;
  all_entities: Entity[];
  source_links: string;
  verification_status: string;
}

// ── 聚合统计 ──────────────────────────────────────────────────────────────────

export interface StatCountry {
  country_iso: string;
  category: string;
  year: number;
  project_count: number;
  total_capacity_mw: number | null;
}

export interface StatEntity {
  primary_entity: string;
  primary_entity_en: string;
  category: string;
  year: number;
  project_count: number;
  total_capacity_mw: number | null;
}

export interface StatTimeline {
  year: number;
  category: string;
  latest_stage: string;
  count: number;
  capacity_mw: number | null;
}

// ── UI 状态 ───────────────────────────────────────────────────────────────────

export type ColorBy = "count" | "capacity";
export type MapLevel = "world" | "province";
export type Lang = "zh" | "en";

export interface FilterState {
  yearRange: [number, number] | null;   // null = 不限年份
  categories: string[];                  // 空数组 = 全选
  stages: string[];                      // 空数组 = 全选
  entities: string[];                    // 空数组 = 全选
  briOnly: boolean;
}

// ── 枚举常量 ──────────────────────────────────────────────────────────────────

export const CATEGORIES: Record<string, { zh: string; en: string; color: string }> = {
  PV: { zh: "光伏",     en: "Solar PV",    color: "#F59E0B" },
  WD: { zh: "风电",     en: "Wind",        color: "#3B82F6" },
  HY: { zh: "水电",     en: "Hydro",       color: "#06B6D4" },
  GT: { zh: "地热及余热", en: "Geothermal",  color: "#EF4444" },
  BM: { zh: "生物质",   en: "Biomass",     color: "#22C55E" },
  ES: { zh: "储能",     en: "Storage",     color: "#8B5CF6" },
  HB: { zh: "综合能源", en: "Hybrid",      color: "#EC4899" },
};

export const STAGES: Record<string, { zh: string; en: string; priority: number }> = {
  "投入运营": { zh: "投入运营", en: "Operational",   priority: 7 },
  "竣工":     { zh: "竣工",     en: "Completed",     priority: 6 },
  "在建":     { zh: "在建",     en: "Under Const.",  priority: 5 },
  "开工":     { zh: "开工",     en: "Construction",  priority: 4 },
  "签约":     { zh: "签约",     en: "Signed",        priority: 3 },
  "中标":     { zh: "中标",     en: "Awarded",       priority: 2 },
  "收购":     { zh: "收购",     en: "Acquired",      priority: 1 },
};

export const STAGE_COLORS: Record<string, string> = {
  "投入运营": "#16A34A",
  "竣工":     "#2563EB",
  "在建":     "#D97706",
  "开工":     "#F59E0B",
  "签约":     "#9333EA",
  "中标":     "#EC4899",
  "收购":     "#6B7280",
};
