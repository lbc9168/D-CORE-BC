import type { Project, VizGroup } from "@/types";
import { statusGroupAt } from "@/contexts/DataContext";
import { hasCapacity } from "@/utils/capacityPolicy";

/** 选定年（null=全部）下的活跃项目集合 */
export function activeProjectsAt(projects: Project[], year: number | null): Project[] {
  if (year == null) return projects;
  return projects.filter(
    (p) =>
      p.first_event_year != null &&
      p.last_event_year != null &&
      p.first_event_year <= year &&
      year <= p.last_event_year,
  );
}

/** 选定年下 4 大类状态分布（year=null 用项目最终状态） */
export function statusMix(projects: Project[], year: number | null): Map<VizGroup, number> {
  const m = new Map<VizGroup, number>();
  for (const p of projects) {
    const g = statusGroupAt(p, year);
    if (g) m.set(g, (m.get(g) ?? 0) + 1);
  }
  return m;
}

/** topCountries 的最小行结构：Project 与 TxMfProject 均满足（count 只用 country_iso） */
export type CountryRow = { country_iso: string; category?: string; capacity_mw?: number | null };

/** Top N 国家（按项目数或装机容量） */
export function topCountries(
  projects: CountryRow[],
  metric: "count" | "capacity",
  n = 12,
): { iso: string; value: number }[] {
  const m = new Map<string, number>();
  for (const p of projects) {
    if (!p.country_iso) continue;
    if (metric === "count") {
      m.set(p.country_iso, (m.get(p.country_iso) ?? 0) + 1);
    } else {
      if (!hasCapacity(p.category)) continue;
      m.set(p.country_iso, (m.get(p.country_iso) ?? 0) + (p.capacity_mw ?? 0));
    }
  }
  return Array.from(m, ([iso, value]) => ({ iso, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, n);
}

/** 大数字格式化：1234 → 1,234；123456 → 123.5K；1234567 → 1.2M */
export function formatBig(v: number): string {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 100_000) return (v / 1_000).toFixed(1) + "K";
  return Math.round(v).toLocaleString("en-US");
}
