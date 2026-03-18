import type { DevStatus } from "@/types";

/**
 * 发达国家 ISO3 列表
 * 基准：OECD 38 成员国（截至 2024 年）
 * 参考：https://www.oecd.org/en/about/members-partners.html
 */
const DEVELOPED_ISO3 = new Set<string>([
  "AUS", "AUT", "BEL", "CAN", "CHL", "COL", "CRI", "CZE",
  "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "ISL",
  "IRL", "ISR", "ITA", "JPN", "KOR", "LVA", "LTU", "LUX",
  "MEX", "NLD", "NZL", "NOR", "POL", "PRT", "SVK", "SVN",
  "ESP", "SWE", "CHE", "TUR", "GBR", "USA",
]);

/**
 * 计算国家发展状态
 * 优先使用 JSON 中已有的 ldc 标记（来自 s03 联合国 LDC 列表），
 * 再依据 OECD 成员资格判断发达/发展中。
 */
export function getDevStatus(
  ldc: string | null | undefined,
  countryIso: string | null | undefined,
): DevStatus {
  if (ldc === "Yes") return "LDC";
  const iso = (countryIso ?? "").trim().toUpperCase();
  if (iso && DEVELOPED_ISO3.has(iso)) return "Developed";
  return "Developing";
}
