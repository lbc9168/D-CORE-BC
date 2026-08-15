/**
 * countryMeta.ts —— 国家级 BRI / LDC 静态表（ISO3）。
 *
 * 生成自 s01b_project_extractor/WorldCountryList.json（bri / ldc 字段），
 * 做法同 continentMapping.ts。用途：tx_flat / mf_flat 行没有 bri_country / ldc
 * 字段，前端按 country_iso 反查（国家级事实，与项目级无关）。
 * 源文件更新后需重新生成（见 docs/rules_and_plans/add_metric_runbook.md）。
 */

/** "一带一路"共建国家 ISO3（155 国） */
export const BRI_ISO3 = new Set<string>([
  "AFG", "AGO", "ALB", "ARE", "ARG", "ARM", "ATG", "AUT", "AZE", "BDI",
  "BEN", "BFA", "BGD", "BGR", "BHR", "BIH", "BLR", "BOL", "BRB", "BRN",
  "BWA", "CAF", "CHL", "CHN", "CIV", "CMR", "COD", "COG", "COK", "COL",
  "COM", "CPV", "CRI", "CUB", "CYP", "CZE", "DJI", "DMA", "DOM", "DZA",
  "ECU", "EGY", "ERI", "ETH", "FJI", "FSM", "GAB", "GEO", "GHA", "GIN",
  "GMB", "GNB", "GNQ", "GRC", "GRD", "GUY", "HKG", "HND", "HRV", "HUN",
  "IDN", "IRN", "IRQ", "JAM", "JOR", "KAZ", "KEN", "KGZ", "KHM", "KIR",
  "KOR", "KWT", "LAO", "LBN", "LBR", "LBY", "LKA", "LSO", "LTU", "LUX",
  "LVA", "MAC", "MAR", "MDA", "MDG", "MDV", "MKD", "MLI", "MLT", "MMR",
  "MNE", "MNG", "MOZ", "MRT", "MUS", "MWI", "MYS", "NAM", "NER", "NGA",
  "NIC", "NIU", "NPL", "NRU", "NZL", "OMN", "PAK", "PER", "PHL", "PNG",
  "POL", "PRT", "QAT", "ROU", "RUS", "RWA", "SAU", "SDN", "SEN", "SGP",
  "SLB", "SLE", "SLV", "SOM", "SRB", "SSD", "STP", "SUR", "SVK", "SVN",
  "SYC", "SYR", "TCD", "TGO", "THA", "TJK", "TKM", "TLS", "TON", "TTO",
  "TUN", "TUR", "TZA", "UGA", "UKR", "URY", "UZB", "VEN", "VNM", "VUT",
  "WSM", "YEM", "ZAF", "ZMB", "ZWE",
]);

/** 联合国最不发达国家（LDC）ISO3（44 国） */
export const LDC_ISO3 = new Set<string>([
  "AFG", "AGO", "BDI", "BEN", "BFA", "BGD", "CAF", "COD", "COM", "DJI",
  "ERI", "ETH", "GIN", "GMB", "GNB", "HTI", "KHM", "KIR", "LAO", "LBR",
  "LSO", "MDG", "MLI", "MMR", "MOZ", "MRT", "MWI", "NER", "NPL", "RWA",
  "SDN", "SEN", "SLB", "SLE", "SOM", "SSD", "TCD", "TGO", "TLS", "TUV",
  "TZA", "UGA", "YEM", "ZMB",
]);
