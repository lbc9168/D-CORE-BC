import {
  CATEGORIES as ALL_CATEGORIES,
  STAGES as ALL_STAGES,
  VIZ_GROUPS as ALL_VIZ_GROUPS,
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
  display_order: number | null;   // s03 按最早事件日期升序赋值（老项目在前），稳定主键 project_id 不受影响
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
  storage_power_mw?: number | null;    // 储能功率（MW），多数项目无值
  storage_energy_mwh?: number | null;  // 储能容量（MWh），多数项目无值
  capacity_source?: string | null;     // 容量来源：original / derived / manual / legacy
  latest_stage: string;
  latest_stage_en: string;
  latest_stage_date: string;
  event_year: number | null;
  event_month: string | null;
  timeline_notes: string;
  stage_events: StageEvent[];        // 4 大类可见事件，按 year 升序（s03 注入）
  first_event_year: number | null;
  last_event_year: number | null;
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

// 4 可视化大类 key —— 必须与 enums_mapping.json 的 viz_group 键保持一致
export type VizGroup =
  | "pre_construction"
  | "under_construction"
  | "completed"
  | "acquired";

export interface StageEvent {
  group: VizGroup;
  year: number;
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

// 事件级时间统计：一个项目在它经历的每个阶段-年份各计一次
export interface StatTimelineEvent {
  year: number;
  month: string | null;
  category: string;
  stage: string;
  country_iso: string;
  project_count: number;
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

// 4 大类元数据（zh/en/color/order），单一可信源 enums_mapping.json
export const VIZ_GROUPS = ALL_VIZ_GROUPS;
// 按 order 排序的 4 大类 key（前期→在建→完工→收购）
export const VIZ_GROUP_KEYS = Object.entries(VIZ_GROUPS)
  .sort(([, a], [, b]) => a.order - b.order)
  .map(([k]) => k) as VizGroup[];
