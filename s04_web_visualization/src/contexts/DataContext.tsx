import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useMemo,
  useCallback,
} from "react";
import type {
  Project,
  StatCountry,
  StatEntity,
  StatTimeline,
  FilterState,
} from "@/types";

// ── 数据加载 ──────────────────────────────────────────────────────────────────

interface RawData {
  projects: Project[];
  statsCountry: StatCountry[];
  statsEntity: StatEntity[];
  statsTimeline: StatTimeline[];
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url}: ${res.status}`);
  return res.json() as Promise<T>;
}

// ── 过滤逻辑 ──────────────────────────────────────────────────────────────────

function applyFilter(projects: Project[], f: FilterState): Project[] {
  return projects.filter((p) => {
    if (f.yearRange && p.event_year != null) {
      if (p.event_year < f.yearRange[0] || p.event_year > f.yearRange[1]) return false;
    }
    if (f.categories.length > 0 && !f.categories.includes(p.category)) return false;
    if (f.stages.length > 0 && !f.stages.includes(p.latest_stage)) return false;
    if (f.entities.length > 0 && !f.entities.includes(p.primary_entity)) return false;
    if (f.briOnly && p.bri_country !== "TRUE" && p.bri_country !== "1" && p.bri_country !== "true") return false;
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

  // 可用选项（来自数据）
  availableYears: number[];
  availableEntities: string[];

  // 加载状态
  loading: boolean;
  error: string | null;
}

// ── 默认过滤状态 ──────────────────────────────────────────────────────────────

const DEFAULT_FILTER: FilterState = {
  yearRange: null,
  categories: [],
  stages: [],
  entities: [],
  briOnly: false,
};

const DataContext = createContext<DataContextValue | null>(null);

// ── Provider ──────────────────────────────────────────────────────────────────

export function DataProvider({ children }: { children: React.ReactNode }) {
  const [raw, setRaw] = useState<RawData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER);

  useEffect(() => {
    Promise.all([
      fetchJson<Project[]>("/data/projects_flat.json"),
      fetchJson<StatCountry[]>("/data/stats_country.json"),
      fetchJson<StatEntity[]>("/data/stats_entity.json"),
      fetchJson<StatTimeline[]>("/data/stats_timeline.json"),
    ])
      .then(([projects, statsCountry, statsEntity, statsTimeline]) => {
        setRaw({ projects, statsCountry, statsEntity, statsTimeline });
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "未知错误");
        setLoading(false);
      });
  }, []);

  const filteredProjects = useMemo(
    () => (raw ? applyFilter(raw.projects, filter) : []),
    [raw, filter],
  );

  const availableYears = useMemo(() => {
    if (!raw) return [];
    const years = new Set<number>();
    raw.projects.forEach((p) => { if (p.event_year != null) years.add(p.event_year); });
    return Array.from(years).sort();
  }, [raw]);

  const availableEntities = useMemo(() => {
    if (!raw) return [];
    const entities = new Set<string>();
    raw.projects.forEach((p) => { if (p.primary_entity) entities.add(p.primary_entity); });
    return Array.from(entities).sort();
  }, [raw]);

  const resetFilter = useCallback(() => setFilter(DEFAULT_FILTER), []);

  const value: DataContextValue = {
    allProjects: raw?.projects ?? [],
    statsCountry: raw?.statsCountry ?? [],
    statsEntity: raw?.statsEntity ?? [],
    statsTimeline: raw?.statsTimeline ?? [],
    filter,
    setFilter,
    resetFilter,
    filteredProjects,
    availableYears,
    availableEntities,
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

/** 按 country_iso 聚合过滤后项目 → {iso: {count, capacity}} */
export function useCountryStats(
  filteredProjects: Project[],
  colorBy: "count" | "capacity",
): Map<string, number> {
  return useMemo(() => {
    const map = new Map<string, number>();
    for (const p of filteredProjects) {
      const iso = p.country_iso;
      if (!iso) continue;
      if (colorBy === "count") {
        map.set(iso, (map.get(iso) ?? 0) + 1);
      } else {
        map.set(iso, (map.get(iso) ?? 0) + (p.capacity_mw ?? 0));
      }
    }
    return map;
  }, [filteredProjects, colorBy]);
}
