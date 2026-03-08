/**
 * 将数值映射到颜色（线性插值，浅蓝 → 深蓝）
 * 无数据时返回 #e2e8f0（slate-200）
 */
export function valueToColor(value: number | undefined, max: number): string {
  if (value == null || value === 0 || max === 0) return "#e2e8f0";

  // 使用对数缩放，减弱头部集中效应
  const ratio = Math.min(Math.log1p(value) / Math.log1p(max), 1);

  // #bae6fd (brand-200) → #0369a1 (brand-700)
  const r = Math.round(186 + (3 - 186) * ratio);
  const g = Math.round(230 + (105 - 230) * ratio);
  const b = Math.round(253 + (161 - 253) * ratio);
  return `rgb(${r},${g},${b})`;
}

/**
 * 计算颜色图例（5 档）
 */
export function buildLegendStops(
  max: number,
  colorBy: "count" | "capacity",
): { value: number; color: string; label: string }[] {
  if (max === 0) return [];
  const stops = [0.1, 0.25, 0.5, 0.75, 1.0];
  return stops.map((ratio) => {
    const value = Math.round(max * ratio);
    return {
      value,
      color: valueToColor(value, max),
      label: colorBy === "capacity" ? `${value.toLocaleString()} MW` : String(value),
    };
  });
}
