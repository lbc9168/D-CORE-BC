const EMPTY_COLOR = "#e2e8f0";
const BIN_COLORS = ["#bfdbfe", "#60a5fa", "#1d4ed8"] as const;
const BIN_COUNT = 3;

export interface ColorBin {
  min: number;
  max: number;
  color: string;
  label: string;
}

function formatValue(value: number, colorBy: "count" | "capacity"): string {
  const rounded = Math.round(value);
  if (colorBy === "capacity") return `${rounded.toLocaleString()} MW`;
  return rounded.toLocaleString();
}

/**
 * 用第二大值估算“易读步长”，并构造 3 档离散分箱。
 * 第三档为开区间（> 2 * binSize）。
 */
export function buildColorBins(values: number[], colorBy: "count" | "capacity"): ColorBin[] {
  const validValues = values
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((a, b) => b - a);
  if (validValues.length === 0) return [];

  const referenceValue = validValues.length > 1 ? validValues[1] : validValues[0];
  const rawBinSize = referenceValue / BIN_COUNT;
  const magnitude = 10 ** Math.floor(Math.log10(rawBinSize));
  const roughBinSize = Math.floor(rawBinSize / magnitude) * magnitude;
  const binSize = Math.max(1, Math.round(roughBinSize));

  return BIN_COLORS.map((color, idx) => {
    let min: number;
    let upper: number | undefined;
    let label: string;

    if (idx === 0) {
      min = 1;
      upper = binSize;
      label = `${formatValue(min, colorBy)} - ${formatValue(upper, colorBy)}`;
    } else if (idx === 1) {
      min = binSize + 1;
      upper = binSize * 2;
      label = `${formatValue(min, colorBy)} - ${formatValue(upper, colorBy)}`;
    } else {
      min = binSize * 2 + 1;
      upper = undefined;
      label = `> ${formatValue(binSize * 2, colorBy)}`;
    }

    return {
      min,
      max: upper ?? Number.POSITIVE_INFINITY,
      color,
      label,
    };
  });
}

/**
 * 将值映射到离散分箱颜色。
 * 无数据（null/undefined/<=0）时返回空颜色。
 */
export function valueToColor(value: number | undefined, bins: ColorBin[]): string {
  if (value == null || value <= 0 || bins.length === 0) return EMPTY_COLOR;

  for (const bin of bins) {
    if (value <= bin.max) return bin.color;
  }
  return bins[bins.length - 1].color;
}
