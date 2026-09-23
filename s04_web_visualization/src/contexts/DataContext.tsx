import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from "react";
import type {
  Project,
  StatCountry,
  StatEntity,
  StatTimeline,
  FilterState,
  VizGroup,
  MetricKey,
  TxProject,
  MfProject,
  TxMfProject,
  Entity,
} from "@/types";
import { ISO3_TO_REGION } from "@/utils/continentMapping";
import { getDevStatus } from "@/utils/devStatus";
import { CATEGORIES, isExcludedCategory, isHiddenCategory } from "@/utils/enums";
import { metricValue, type MetricRow } from "@/utils/metrics";
import { BRI_ISO3, LDC_ISO3 } from "@/utils/countryMeta";

// ── 数据加载 ──────────────────────────────────────────────────────────────────
const EXCLUDED_ISO3 = new Set(["CHN", "HKG", "MAC", "TWN"]);
// 基线年份下限：早于此年的项目/事件不进入可视化（与 s03 口径一致）
export const BASELINE_YEAR = 2022;
const UNKNOWN_TEXTS = new Set(["unknown", "未知", "未披露", "n/a", "na", "-", "—"]);

function isUnknownText(value: string | null | undefined): boolean {
  if (!value) return true;
  const normalized = value.trim().toLowerCase();
  if (!normalized) return true;
  return UNKNOWN_TEXTS.has(normalized);
}

/** 类别筛选兼容标准中文键与旧/异常导出中的英文类别值。 */
function matchesSelectedCategory(p: Project, selected: string[]): boolean {
  return selected.some((categoryKey) =>
    p.category === categoryKey || p.category_en === CATEGORIES[categoryKey]?.en,
  );
}

function shouldExcludeByPolicy(p: Project): boolean {
  const iso = (p.country_iso || "").toUpperCase();
  if (EXCLUDED_ISO3.has(iso)) return true;
  // 无可见 stage 事件、或最晚事件早于 2022 → 排除（与旧 2022+ 基线一致）
  if (p.first_event_year == null || p.last_event_year == null) return true;
  if (p.last_event_year < BASELINE_YEAR) return true;
  if (isExcludedCategory(p.category) || isHiddenCategory(p.category)) return true;
  if (isUnknownText(p.primary_entity)) return true;
  // Missing capacity does not invalidate accepted project participation.
  return false;
}

function applyBaselinePolicy(projects: Project[]): Project[] {
  return projects.filter((p) => !shouldExcludeByPolicy(p));
}

interface Metadata {
  last_updated: string;
  project_count: number;
}

interface RawData {
  projects: Project[];
  statsCountry: StatCountry[];
  statsEntity: StatEntity[];
  statsTimeline: StatTimeline[];
  lastUpdated: string;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url}: ${res.status}`);
  return res.json() as Promise<T>;
}

// ── 过滤逻辑 ──────────────────────────────────────────────────────────────────

/** 截至 year 的最新事件所属大类；year 为 null 取总体最新；无可见事件返回 null。 */
export function statusGroupAt(p: Project, year: number | null): VizGroup | null {
  const evs = p.stage_events;
  if (!evs || evs.length === 0) return null;
  if (year == null) return evs[evs.length - 1].group; // stage_events 已按 year 升序
  let g: VizGroup | null = null;
  for (const e of evs) {
    if (e.year <= year) g = e.group;
    else break;
  }
  return g;
}

function applyFilter(projects: Project[], f: FilterState): Project[] {
  return projects.filter((p) => {
    // 区间存在性：项目活动区间 [first,last] 须与所选范围 [start,end] 有交叠
    if (f.yearRange != null) {
      if (p.first_event_year == null || p.last_event_year == null) return false;
      const [start, end] = f.yearRange;
      if (p.last_event_year < start || p.first_event_year > end) return false;
    }
    if (f.categories.length > 0 && !matchesSelectedCategory(p, f.categories)) return false;
    // 项目形态子类筛选：当前仅光伏集中式/分布式。
    if (f.subcategories.length > 0 && !f.subcategories.includes(p.subcategory ?? "")) return false;
    // 按状态筛选：状态取范围结束年（end）的快照
    if (f.groups.length > 0) {
      const g = statusGroupAt(p, f.yearRange ? f.yearRange[1] : null);
      if (g == null || !f.groups.includes(g)) return false;
    }
    if (f.entities.length > 0 && !f.entities.includes(p.primary_entity)) return false;
    if (f.briFilter.length === 1 && p.bri_country !== f.briFilter[0]) return false;
    if (f.ldcFilter.length > 0 && !f.ldcFilter.includes(p.dev_status ?? "Developing")) return false;
    if (f.soeFilter === "soe" && p.has_soe !== 1) return false;
    if (f.soeFilter === "non_soe" && p.has_soe === 1) return false;
    if (f.regionFilter) {
      const region = ISO3_TO_REGION[p.country_iso ?? ""];
      if (!region || region.continent !== f.regionFilter.continent) return false;
      if (f.regionFilter.subregion && region.subregion !== f.regionFilter.subregion) return false;
    }
    return true;
  });
}

// ── TX/MF 专表（tx_flat/mf_flat 与 Project 结构不同，不走 baseline policy）────────

/**
 * 原始行 → 补齐派生字段（kind / bri / ldc / dev_status / has_soe）。
 * 基线口径与主表一致：剔除中港澳台 + 终态事件年早于 BASELINE_YEAR（专表 event_year
 * 即"最新事件年"，同构于主表 last_event_year < 2022 的排除）。
 */
function enrichTxMf<K extends "tx" | "mf">(
  rows: Array<Record<string, unknown>>,
  kind: K,
): Array<K extends "tx" ? TxProject : MfProject> {
  return rows
    .filter((r) => !EXCLUDED_ISO3.has(String(r.country_iso ?? "").toUpperCase()))
    .filter((r) => typeof r.event_year === "number" && r.event_year >= BASELINE_YEAR)
    .map((r) => {
      const iso = String(r.country_iso ?? "").toUpperCase();
      const entities = (r.all_entities ?? []) as Entity[];
      const ldc = LDC_ISO3.has(iso) ? "Yes" : "No";
      return {
        ...r,
        kind,
        bri_country: BRI_ISO3.has(iso) ? "Yes" : "No",
        ldc,
        dev_status: getDevStatus(ldc, iso),
        has_soe: entities.some((e) => e.is_central_soe === 1) ? 1 : 0,
      } as unknown as K extends "tx" ? TxProject : MfProject;
    });
}

/** txmf 域过滤：年份为单点包含（专表只有终态 event_year，无时间线区间） */
function applyTxMfFilter(rows: TxMfProject[], f: FilterState): TxMfProject[] {
  return rows.filter((p) => {
    if (f.txmfKind !== "all" && p.kind !== f.txmfKind) return false;
    if (f.yearRange != null) {
      if (p.event_year == null) return false;
      if (p.event_year < f.yearRange[0] || p.event_year > f.yearRange[1]) return false;
    }
    if (f.briFilter.length === 1 && p.bri_country !== f.briFilter[0]) return false;
    if (f.ldcFilter.length > 0 && !f.ldcFilter.includes(p.dev_status)) return false;
    if (f.soeFilter === "soe" && p.has_soe !== 1) return false;
    if (f.soeFilter === "non_soe" && p.has_soe === 1) return false;
    if (f.regionFilter) {
      const region = ISO3_TO_REGION[p.country_iso ?? ""];
      if (!region || region.continent !== f.regionFilter.continent) return false;
      if (f.regionFilter.subregion && region.subregion !== f.regionFilter.subregion) return false;
    }
    return true;
  });
}

// ── 上下文类型 ────────────────────────────────────────────────────────────────

interface DataContextValue {
  // 原始数据
  allProjects: Project[];
  statsCountry: StatCountry[];
  statsEntity: StatEntity[];
  statsTimeline: StatTimeline[];

  // 过滤状态
  filter: FilterState;
  setFilter: (f: FilterState | ((prev: FilterState) => FilterState)) => void;
  resetFilter: () => void;

  // 过滤后数据
  filteredProjects: Project[];
  statusGroupOf: (p: Project) => VizGroup | null;

  // TX/MF 专表（懒加载：首次调用 ensureTxMfLoaded 时才 fetch）
  txProjects: TxProject[];
  mfProjects: MfProject[];
  txmfLoading: boolean;
  ensureTxMfLoaded: () => void;
  filteredTxMf: TxMfProject[];

  // 可用选项（来自数据）
  availableYears: number[];
  availableEntities: string[];

  // 数据更新日期
  lastUpdated: string;

  // 加载状态
  loading: boolean;
  error: string | null;
}

// ── 默认过滤状态 ──────────────────────────────────────────────────────────────

const DEFAULT_FILTER: FilterState = {
  yearRange: null,
  categories: [],
  subcategories: [],
  groups: [],
  entities: [],
  briFilter: [],
  ldcFilter: [],
  soeFilter: "all",
  regionFilter: null,
  txmfKind: "all",
};

const DataContext = createContext<DataContextValue | null>(null);

// ── Provider ──────────────────────────────────────────────────────────────────

export function DataProvider({ children }: { children: React.ReactNode }) {
  const [raw, setRaw] = useState<RawData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER);

  // TX/MF 专表懒加载状态（ref 防重复 fetch；失败允许下次重试）
  const [txProjects, setTxProjects] = useState<TxProject[]>([]);
  const [mfProjects, setMfProjects] = useState<MfProject[]>([]);
  const [txmfLoading, setTxmfLoading] = useState(false);
  const txmfRequested = useRef(false);

  const ensureTxMfLoaded = useCallback(() => {
    if (txmfRequested.current) return;
    txmfRequested.current = true;
    setTxmfLoading(true);
    const base = import.meta.env.BASE_URL;
    Promise.all([
      fetchJson<Array<Record<string, unknown>>>(`${base}data/tx_flat.json`),
      fetchJson<Array<Record<string, unknown>>>(`${base}data/mf_flat.json`),
    ])
      .then(([tx, mf]) => {
        setTxProjects(enrichTxMf(tx, "tx"));
        setMfProjects(enrichTxMf(mf, "mf"));
        setTxmfLoading(false);
      })
      .catch((e: unknown) => {
        console.error("[DataContext] tx/mf_flat 加载失败:", e);
        txmfRequested.current = false; // 允许重试
        setTxmfLoading(false);
      });
  }, []);

  useEffect(() => {
    const base = import.meta.env.BASE_URL;
    Promise.all([
      fetchJson<Project[]>(`${base}data/projects_flat.json`),
      fetchJson<StatCountry[]>(`${base}data/stats_country.json`),
      fetchJson<StatEntity[]>(`${base}data/stats_entity.json`),
      fetchJson<StatTimeline[]>(`${base}data/stats_timeline.json`),
      fetchJson<Metadata>(`${base}data/metadata.json`).catch(() => ({ last_updated: "", project_count: 0 })),
    ])
      .then(([projects, statsCountry, statsEntity, statsTimeline, meta]) => {
        // 计算每个项目的发展状态（LDC / Developing / Developed）
        const enriched = projects.map((p) => ({
          ...p,
          dev_status: getDevStatus(p.ldc, p.country_iso),
        }));
        setRaw({ projects: enriched, statsCountry, statsEntity, statsTimeline, lastUpdated: meta.last_updated ?? "" });
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "未知错误");
        setLoading(false);
      });
  }, []);

  const policyProjects = useMemo(
    () => (raw ? applyBaselinePolicy(raw.projects) : []),
    [raw],
  );

  const filteredProjects = useMemo(
    () => applyFilter(policyProjects, filter),
    [policyProjects, filter],
  );

  const filteredTxMf = useMemo(
    () => applyTxMfFilter([...txProjects, ...mfProjects], filter),
    [txProjects, mfProjects, filter],
  );

  const availableYears = useMemo(() => {
    if (!policyProjects.length) return [];
    const years = new Set<number>();
    policyProjects.forEach((p) =>
      p.stage_events?.forEach((e) => { if (e.year >= BASELINE_YEAR) years.add(e.year); }),
    );
    return Array.from(years).sort();
  }, [policyProjects]);

  const availableEntities = useMemo(() => {
    if (!policyProjects.length) return [];
    const entities = new Set<string>();
    policyProjects.forEach((p) => { if (p.primary_entity) entities.add(p.primary_entity); });
    return Array.from(entities).sort();
  }, [policyProjects]);

  const resetFilter = useCallback(() => setFilter(DEFAULT_FILTER), []);

  const value: DataContextValue = {
    allProjects: policyProjects,
    statsCountry: raw?.statsCountry ?? [],
    statsEntity: raw?.statsEntity ?? [],
    statsTimeline: raw?.statsTimeline ?? [],
    filter,
    setFilter,
    resetFilter,
    filteredProjects,
    statusGroupOf: (p: Project) => statusGroupAt(p, filter.yearRange ? filter.yearRange[1] : null),
    txProjects,
    mfProjects,
    txmfLoading,
    ensureTxMfLoaded,
    filteredTxMf,
    availableYears,
    availableEntities,
    lastUpdated: raw?.lastUpdated ?? "",
    loading,
    error,
  };

  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function useData(): DataContextValue {
  const ctx = useContext(DataContext);
  if (!ctx) throw new Error("useData must be used within DataProvider");
  return ctx;
}

/** 按 country_iso 聚合行 → {iso: 度量值}；度量语义（含 capacity 的 hasCapacity 守卫）收口在 metrics.ts */
export function useCountryStats(
  rows: Array<{ country_iso: string } & MetricRow>,
  metric: MetricKey,
): Map<string, number> {
  return useMemo(() => {
    const map = new Map<string, number>();
    for (const p of rows) {
      const iso = p.country_iso;
      if (!iso) continue;
      const v = metricValue(metric, p);
      if (v == null) continue;
      map.set(iso, (map.get(iso) ?? 0) + v);
    }
    return map;
  }, [rows, metric]);
}
