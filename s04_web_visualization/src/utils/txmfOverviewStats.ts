/**
 * txmfOverviewStats.ts —— Overview 产业链侧（TX/MF 专表）的统计纯函数。
 * 专表只有终态快照（无 stage_events 区间），全部统计为 count 口径；
 * 年份筛选语义 = 终态事件年恰为该年（区别于主表的"区间覆盖"）。
 */
import type { TxMfProject, VizGroup } from "@/types";
import { stageToGroup } from "@/utils/enums";

/** 选定年（null=全部）下的专表行：终态事件年 === year */
export function txmfAtYear(rows: TxMfProject[], year: number | null): TxMfProject[] {
  if (year == null) return rows;
  return rows.filter((r) => r.event_year === year);
}

/** KPI：输变电 / 制造业 / 覆盖国家数 / 参与中企数（实体名去重，name_en 优先） */
export function txmfKpis(rows: TxMfProject[]) {
  const countries = new Set<string>();
  const entities = new Set<string>();
  let tx = 0;
  let mf = 0;
  for (const r of rows) {
    if (r.kind === "tx") tx++;
    else mf++;
    if (r.country_iso) countries.add(r.country_iso);
    for (const e of r.all_entities ?? []) {
      const name = (e.name_en || e.name || "").trim();
      if (name) entities.add(name);
    }
  }
  return { tx, mf, countries: countries.size, entities: entities.size };
}

/** TX/MF 子域筛选（图表级控件用） */
export function filterTxMfKind(rows: TxMfProject[], kind: "all" | "tx" | "mf"): TxMfProject[] {
  if (kind === "all") return rows;
  return rows.filter((r) => r.kind === kind);
}

/** 当前状态 4 大类分布（stageToGroup(latest_stage)，无"某年快照"能力） */
export function txmfStatusMix(rows: TxMfProject[]): Map<VizGroup, number> {
  const m = new Map<VizGroup, number>();
  for (const r of rows) {
    const g = stageToGroup(r.latest_stage);
    if (g) m.set(g, (m.get(g) ?? 0) + 1);
  }
  return m;
}

/** Top N 中方实体（按参与项目数；同一项目同名实体只计一次） */
export function txmfTopEntities(rows: TxMfProject[], n = 5): { name: string; value: number }[] {
  const m = new Map<string, number>();
  for (const r of rows) {
    const seen = new Set<string>();
    for (const e of r.all_entities ?? []) {
      const name = (e.name_en || e.name || "").trim();
      if (!name || seen.has(name)) continue;
      seen.add(name);
      m.set(name, (m.get(name) ?? 0) + 1);
    }
  }
  return Array.from(m, ([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, n);
}
