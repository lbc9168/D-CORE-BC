// ── 项目数据 ──────────────────────────────────────────────────────────────────

export interface Entity {
  name: string;
  name_en: string;
  subsidiary: string;
  subsidiary_en: string;
  involvement_type: string;
  involvement_type_en: string;
  is_central_soe: 0 | 1 | null;
}

export interface Project {
  project_id: string;
  project_name: string;
  project_name_en: string;
  name_en_quality: string;
  country: string;
  country_en?: string;      // English country name (populated after s03 rerun)
  country_iso: string;      // ISO 3166-1 alpha-3
  bri_country: string;
  ldc: string;
  location: string;
  province: string | null;
  category: string;         // 光伏 / 风电 / 水电 / 核电 / 火电 / 地热及余热 / 生物质 / 储能 / 综合能源 / 新能源制造业 / 输变电建设
  category_en: string;      // Solar PV / Wind Power / Hydropower / ...
  capacity_mw: number | null;
  latest_stage: string;
  latest_stage_en: string;
  latest_stage_date: string;
  event_year: number | null;
  event_month: string | null;
  timeline_notes: string;
  event_count: number;
  total_investment_usd: number | null;
  financing_model: string;
  financing_model_en: string;
  official_website_links?: string[];
  non_official_website_links?: string[];
  primary_entity: string;
  primary_entity_en: string;
  all_entities: Entity[];
  has_soe: 0 | 1;
  verification_status: string;
  record_type: string;
  dev_status?: DevStatus;   // 计算字段，由 DataContext 基于 ldc + country_iso 填充
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
export type DevStatus = "LDC" | "Developing" | "Developed";

export interface FilterState {
  yearRange: [number, number] | null;   // null = 不限年份
  categories: string[];                  // 空数组 = 全选
  stages: string[];                      // 空数组 = 全选
  entities: string[];                    // 空数组 = 全选
  briFilter: string[];                   // [] 或 ["Yes","No"] = 不过滤；["Yes"] = 仅BRI；["No"] = 仅非BRI
  ldcFilter: string[];                   // [] 或 ["Yes","No"] = 不过滤；["Yes"] = 仅LDC；["No"] = 仅非LDC
  soeFilter: "all" | "soe" | "non_soe"; // SOE 筛选："all" = 全部；"soe" = 仅含央企；"non_soe" = 不含央企
}

// ── 枚举常量 ──────────────────────────────────────────────────────────────────

export const CATEGORIES: Record<string, { zh: string; en: string; color: string }> = {
  "光伏":         { zh: "光伏",         en: "Solar PV",        color: "#F59E0B" },
  "风电":         { zh: "风电",         en: "Wind",            color: "#3B82F6" },
  "水电":         { zh: "水电",         en: "Hydro",           color: "#06B6D4" },
  "核电":         { zh: "核电",         en: "Nuclear",         color: "#8B5CF6" },
  "火电":         { zh: "火电",         en: "Thermal",         color: "#78716C" },
  "地热及余热":   { zh: "地热及余热",   en: "Geothermal",      color: "#10B981" },
  "生物质":       { zh: "生物质",       en: "Biomass",         color: "#84CC16" },
  "储能":         { zh: "储能",         en: "Storage",         color: "#64748B" },
  "综合能源":     { zh: "综合能源",     en: "Hybrid",          color: "#EC4899" },
  "新能源制造业": { zh: "新能源制造业", en: "New Energy Mfg.", color: "#A78BFA" },
  "输变电建设":   { zh: "输变电建设",   en: "Grid Infra.",     color: "#F97316" },
};

export const STAGES: Record<string, { zh: string; en: string; priority: number }> = {
  "投入运营": { zh: "投入运营", en: "Operational",        priority: 7 },
  "竣工":     { zh: "竣工",     en: "Completed",          priority: 6 },
  "在建":     { zh: "在建",     en: "Under Construction", priority: 5 },
  "开工":     { zh: "开工",     en: "Construction Start", priority: 4 },
  "签约":     { zh: "签约",     en: "Signed",             priority: 3 },
  "中标":     { zh: "中标",     en: "Awarded",            priority: 2 },
  "收购":     { zh: "收购",     en: "Acquired",           priority: 1 },
  "拟开发":   { zh: "拟开发",   en: "Planned",            priority: 0 },
};

export const STAGE_COLORS: Record<string, string> = {
  "投入运营": "#16A34A",
  "竣工":     "#2563EB",
  "在建":     "#D97706",
  "开工":     "#F59E0B",
  "签约":     "#9333EA",
  "中标":     "#EC4899",
  "收购":     "#6B7280",
  "拟开发":   "#94a3b8",
};
