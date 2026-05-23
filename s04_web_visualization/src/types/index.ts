import {
  CATEGORIES as ALL_CATEGORIES,
  STAGES as ALL_STAGES,
} from "@/utils/enums";

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

export interface RegionFilter {
  continent: string;
  subregion: string | null;  // null = 整个大洲
}

export interface FilterState {
  yearRange: [number, number] | null;   // null = 不限年份
  categories: string[];                  // 空数组 = 全选
  stages: string[];                      // 空数组 = 全选
  entities: string[];                    // 空数组 = 全选
  briFilter: string[];                   // [] 或 ["Yes","No"] = 不过滤；["Yes"] = 仅BRI；["No"] = 仅非BRI
  ldcFilter: string[];                   // [] 或 ["Yes","No"] = 不过滤；["Yes"] = 仅LDC；["No"] = 仅非LDC
  soeFilter: "all" | "soe" | "non_soe"; // SOE 筛选："all" = 全部；"soe" = 仅含央企；"non_soe" = 不含央企
  regionFilter: RegionFilter | null;     // null = 全球；否则按大洲/小区过滤
}

// ── 枚举常量（派生自单一可信源 @/utils/enums；剔除 UI 不展示的 Unknown 键）──────

function omitUnknown<T>(o: Record<string, T>): Record<string, T> {
  return Object.fromEntries(Object.entries(o).filter(([k]) => k !== "Unknown"));
}

export const CATEGORIES = omitUnknown(ALL_CATEGORIES);
export const STAGES = omitUnknown(ALL_STAGES);
export const STAGE_COLORS: Record<string, string> = Object.fromEntries(
  Object.entries(STAGES).map(([k, v]) => [k, v.color]),
);
