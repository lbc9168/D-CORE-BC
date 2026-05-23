import type { Project } from "@/types";

const CSV_COLUMNS: { key: keyof Project; label: string }[] = [
  { key: "display_order",     label: "Order" },
  { key: "project_id",        label: "Project ID" },
  { key: "project_name_en",   label: "Project Name (EN)" },
  { key: "country_en",        label: "Country" },
  { key: "country_iso",       label: "Country ISO3" },
  { key: "bri_country",       label: "BRI Country" },
  { key: "ldc",               label: "LDC" },
  { key: "province",          label: "Province/State" },
  { key: "category_en",       label: "Energy Category" },
  { key: "capacity_mw",       label: "Capacity (MW)" },
  { key: "latest_stage_en",   label: "Latest Stage" },
  { key: "event_year",        label: "Year" },
  { key: "event_month",       label: "Month" },
  { key: "primary_entity_en", label: "Primary Chinese Entity (EN)" },
  { key: "has_soe",           label: "Central SOE Involved" },
  { key: "total_investment_usd", label: "Total Investment (USD M)" },
  { key: "financing_model_en",   label: "Financing Model" },
];

function escapeField(val: unknown): string {
  if (val == null) return "";
  const s = String(val);
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

/** 将项目数组转换为 CSV 字符串（含 UTF-8 BOM，Excel 直接打开中文不乱码）。 */
export function projectsToCSV(projects: Project[]): string {
  const header = CSV_COLUMNS.map((c) => c.label).join(",");
  const rows = projects.map((p) =>
    CSV_COLUMNS.map((c) => escapeField(p[c.key])).join(","),
  );
  return "\uFEFF" + [header, ...rows].join("\r\n");
}

/** 触发浏览器下载 CSV 文件。 */
export function downloadCSV(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
