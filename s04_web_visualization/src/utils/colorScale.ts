/**
 * 将数值映射到颜色（3 档离散色阶）
 * 无数据时返回 #e2e8f0（slate-200）
 */
export function valueToColor(value: number | undefined, max: number): string {
  if (value == null || value === 0 || max === 0) return "#e2e8f0";

  const ratio = Math.min(Math.log1p(value) / Math.log1p(max), 1);
  if (ratio <= 0.33) return "#bfdbfe";
  if (ratio <= 0.66) return "#60a5fa";
  return "#1d4ed8";
}

/**
 * 计算颜色图例（3 档）
 */
export function buildLegendStops(
  max: number,
  colorBy: "count" | "capacity",
): { value: number; color: string; label: string }[] {
  if (max === 0) return [];
  const stops = [0.33, 0.66, 1.0];
  return stops.map((ratio) => {
    const value = Math.max(1, Math.round(max * ratio));
    return {
      value,
      color: valueToColor(value, max),
      label: colorBy === "capacity" ? `${value.toLocaleString()} MW` : String(value),
    };
  });
}
