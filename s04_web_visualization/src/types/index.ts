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

/** Curated reading links; their scope does not imply verification of every project fact. */
export interface ExternalReference {
  reference_id: string;
  url: string;
  title: string;
  publisher: string;
  source_type: "official" | "media";
  scope: "event" | "project";
  event_id: string | null;
  stage: string | null;
  stage_en: string | null;
  event_date: string | null;
  checked_at: string;
  valid_until: string;
  is_reprint: boolean;
  original_publisher: string | null;
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
  subcategory?: string;     // 项目形态子类中文键（当前仅光伏：集中式/分布式）
  subcategory_en?: string;  // 项目形态子类英文标签（Utility-scale / Distributed）
  generation_sources: string[];   // 发电源原子事实（储能不计为发电源）
  storage_status: "confirmed" | "planned" | "optional" | "unclear" | "not_mentioned";
  technology_tags: string[];      // 技术组合标签中文键（如光储/风光储）
  technology_tags_en: string[];   // 技术组合标签英文展示值
  capacity_mw: number | null;
  pv_capacity_mw?: number | null;      // 综合能源项目光伏子项装机（MW），其余类别无值
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
  contract_amount_usd?: number | null;   // 合同金额（百万美元），仅新数据，历史留空
  financing_amount_usd?: number | null;  // 融资额度（百万美元），仅新数据，历史留空
  financing_model: string;
  financing_model_en: string;
  official_website_links?: string[];
  non_official_website_links?: string[];
  external_references?: ExternalReference[];
  primary_entity: string;
  primary_entity_en: string;
  project_developer: string;        // 业主中文
  project_developer_en: string;     // 业主英文
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

// ── TX/MF 专表（tx_flat.json / mf_flat.json，与 Project 结构不同，勿混用）────────

/** tx_flat / mf_flat 公共列（终态事件快照，无时间线/融资/BRI/LDC 字段） */
interface TxMfCommon {
  project_id: string;
  project_name: string;
  project_name_en: string;
  country: string;
  country_en?: string;
  country_iso: string;
  province: string | null;
  latest_stage: string;
  latest_stage_en: string;
  event_year: number | null;
  all_entities: Entity[];
  external_references?: ExternalReference[];
  // ── 前端加载时派生（数据文件本身不含）──
  bri_country: string;      // 按 country_iso 查 countryMeta 静态表
  ldc: string;              // 同上
  dev_status: DevStatus;    // getDevStatus(ldc, iso)
  has_soe: 0 | 1;           // all_entities 任一 is_central_soe===1
}

export interface TxProject extends TxMfCommon {
  kind: "tx";               // 判别式字面量（加载时注入），type guard 用
  voltage_kv: number | null;
  line_length_km: number | null;
  transmission_capacity_mw: number | null;
}

export interface MfProject extends TxMfCommon {
  kind: "mf";
  production_capacity_value: number | null;
  production_capacity_unit: string | null;  // Public export units: GW/year, GWh/year, t/year, Unknown
}

export type TxMfProject = TxProject | MfProject;

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

/** 地图/排名的着色度量。count 三域通用；capacity_mw 仅发电域；storage_* 仅储能域 */
export type MetricKey = "count" | "capacity_mw" | "storage_power_mw" | "storage_energy_mwh";
/** Map 页数据域：两套互斥数据集——新能源发电（含储能，现状口径） / 输变电·制造业专表。
 *  储能不是独立域而是发电域的"度量透镜"（选储能度量时列表聚焦储能相关子集），见 utils/domains.ts */
export type DomainKey = "renewable" | "txmf";
export type MapLevel = "world" | "province";
export type Lang = "zh" | "en";
export type DevStatus = "LDC" | "Developing" | "Developed";

export interface RegionFilter {
  continent: string;
  subregion: string | null;  // null = 整个大洲
}

export interface FilterState {
  yearRange: [number, number] | null;    // 年份范围 [start, end]；null = 不限年份。状态/配色按 end 年（结束年）计算
  categories: string[];                  // 空数组 = 全选
  subcategories: string[];               // 项目形态子类中文键（集中式/分布式）；空数组 = 不筛选
  groups: VizGroup[];                    // 4 大类状态；空数组 = 全选
  entities: string[];                    // 空数组 = 全选
  briFilter: string[];                   // [] 或 ["Yes","No"] = 不过滤；["Yes"] = 仅BRI；["No"] = 仅非BRI
  ldcFilter: string[];                   // [] 或 ["Yes","No"] = 不过滤；["Yes"] = 仅LDC；["No"] = 仅非LDC
  soeFilter: "all" | "soe" | "non_soe"; // SOE 筛选："all" = 全部；"soe" = 仅含央企；"non_soe" = 不含央企
  regionFilter: RegionFilter | null;     // null = 全球；否则按大洲/小区过滤
  txmfKind: "all" | "tx" | "mf";         // 仅 txmf 域生效：输变电/制造业子域切换
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
