import zh from "./zh";
import en from "./en";
import type { Translations } from "./zh";
import type { Lang } from "@/types";

export type { Translations };

export const translations: Record<Lang, Translations> = { zh, en };

/**
 * 简单插值：将 {{key}} 替换为 values[key]
 * 例：t("table.total", { count: 42 }) → "共 42 条"
 */
export function interpolate(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{\{(\w+)\}\}/g, (_, key) => String(values[key] ?? ""));
}
