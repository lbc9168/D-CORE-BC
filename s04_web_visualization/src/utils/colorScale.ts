// 无数据国家/省份的填充色：中性冷灰，刻意脱离蓝绿色阶，与最浅档明显区分（世界图+省级图共用）
export const EMPTY_COLOR = "#CBD5E1";
const BIN_COLORS = ["#93C4C2", "#5A9896", "#2F6562"] as const;
const BIN_COUNT = 3;

export interface ColorBin {
  min: number;
  max: number;
  color: string;
  label: string;
}

/** 数值 + 单位后缀（unit 为空串 = 纯计数，不带单位） */
function formatValue(value: number, unit: string): string {
  const rounded = Math.round(value);
  return unit ? `${rounded.toLocaleString()} ${unit}` : rounded.toLocaleString();
}

/**
 * 用第二大值估算“易读步长”，并构造 3 档离散分箱。
 * 第三档为开区间（> 2 * binSize）。unit 只影响图例文案（"MW"/"MWh"/""）。
 */
export function buildColorBins(values: number[], unit: string): ColorBin[] {
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
      label = `${formatValue(min, unit)} - ${formatValue(upper, unit)}`;
    } else if (idx === 1) {
      min = binSize + 1;
      upper = binSize * 2;
      label = `${formatValue(min, unit)} - ${formatValue(upper, unit)}`;
    } else {
      min = binSize * 2 + 1;
      upper = undefined;
      label = `> ${formatValue(binSize * 2, unit)}`;
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
