import type { Lang } from "@/types";

const COUNTRY_NAME_OVERRIDES: Record<string, { zh: string; en: string }> = {
  CHN: { zh: "中国", en: "China" },
  HKG: { zh: "中国香港", en: "Hong Kong, China" },
  MAC: { zh: "中国澳门", en: "Macao, China" },
  TWN: { zh: "中国台湾", en: "Taiwan, China" },
};

export function formatCountryName(
  country: string | null | undefined,
  iso3: string | null | undefined,
  lang: Lang,
): string {
  const fallback = country || "—";
  if (!iso3) return fallback;

  const code = iso3.trim().toUpperCase();
  if (COUNTRY_NAME_OVERRIDES[code]) {
    return COUNTRY_NAME_OVERRIDES[code][lang];
  }

  return fallback;
}
