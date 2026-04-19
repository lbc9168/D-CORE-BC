/**
 * ISO 3166-1 数字代码 → alpha-3 代码映射
 * 供 world-atlas TopoJSON（数字 ID）与项目数据（alpha-3）对应
 */
export const NUMERIC_TO_ISO3: Record<string, string> = {
  "4": "AFG", "8": "ALB", "12": "DZA", "16": "ASM", "20": "AND",
  "24": "AGO", "28": "ATG", "31": "AZE", "32": "ARG", "36": "AUS",
  "40": "AUT", "44": "BHS", "48": "BHR", "50": "BGD", "51": "ARM",
  "52": "BRB", "56": "BEL", "60": "BMU", "64": "BTN", "68": "BOL",
  "70": "BIH", "72": "BWA", "76": "BRA", "84": "BLZ", "90": "SLB",
  "96": "BRN", "100": "BGR", "104": "MMR", "108": "BDI", "112": "BLR",
  "116": "KHM", "120": "CMR", "124": "CAN", "132": "CPV", "140": "CAF",
  "144": "LKA", "152": "CHL", "156": "CHN", "170": "COL", "174": "COM",
  "178": "COG", "180": "COD", "188": "CRI", "191": "HRV", "192": "CUB",
  "196": "CYP", "203": "CZE", "204": "BEN", "208": "DNK", "214": "DOM",
  "218": "ECU", "222": "SLV", "226": "GNQ", "231": "ETH", "232": "ERI",
  "233": "EST", "242": "FJI", "246": "FIN", "250": "FRA", "266": "GAB",
  "268": "GEO", "270": "GMB", "276": "DEU", "288": "GHA", "300": "GRC",
  "308": "GRD", "320": "GTM", "324": "GIN", "328": "GUY", "332": "HTI",
  "340": "HND", "348": "HUN", "352": "ISL", "356": "IND", "360": "IDN",
  "364": "IRN", "368": "IRQ", "372": "IRL", "376": "ISR", "380": "ITA",
  "388": "JAM", "392": "JPN", "400": "JOR", "398": "KAZ", "404": "KEN",
  "408": "PRK", "410": "KOR", "414": "KWT", "417": "KGZ", "418": "LAO",
  "422": "LBN", "426": "LSO", "428": "LVA", "430": "LBR", "434": "LBY",
  "440": "LTU", "442": "LUX", "450": "MDG", "454": "MWI", "458": "MYS",
  "462": "MDV", "466": "MLI", "470": "MLT", "478": "MRT", "480": "MUS",
  "484": "MEX", "496": "MNG", "498": "MDA", "504": "MAR", "508": "MOZ",
  "516": "NAM", "524": "NPL", "528": "NLD", "540": "NCL", "558": "NIC",
  "562": "NER", "566": "NGA", "578": "NOR", "586": "PAK", "591": "PAN",
  "598": "PNG", "600": "PRY", "604": "PER", "608": "PHL", "616": "POL",
  "620": "PRT", "630": "PRI", "634": "QAT", "642": "ROU", "643": "RUS",
  "646": "RWA", "682": "SAU", "686": "SEN", "694": "SLE", "703": "SVK",
  "705": "SVN", "706": "SOM", "710": "ZAF", "716": "ZWE", "724": "ESP",
  "728": "SSD", "729": "SDN", "740": "SUR", "752": "SWE", "756": "CHE",
  "760": "SYR", "762": "TJK", "764": "THA", "768": "TGO", "780": "TTO",
  "784": "ARE", "788": "TUN", "792": "TUR", "795": "TKM", "800": "UGA",
  "804": "UKR", "818": "EGY", "826": "GBR", "834": "TZA", "840": "USA",
  "854": "BFA", "858": "URY", "860": "UZB", "862": "VEN", "882": "WSM",
  "887": "YEM", "894": "ZMB", "548": "VUT", "520": "NRU",
  "690": "SYC",
  // ── 补充缺失的国家 ───────────────────────────────────────────────────────────
  "148": "TCD",   // 乍得 Chad
  "262": "DJI",   // 吉布提 Djibouti
  "296": "KIR",   // 基里巴斯 Kiribati
  "384": "CIV",   // 科特迪瓦 Côte d'Ivoire
  "499": "MNE",   // 黑山 Montenegro
  "512": "OMN",   // 阿曼 Oman
  "554": "NZL",   // 新西兰 New Zealand
  "583": "FSM",   // 密克罗尼西亚 Micronesia
  "584": "MHL",   // 马绍尔群岛 Marshall Islands
  "624": "GNB",   // 几内亚比绍 Guinea-Bissau
  "626": "TLS",   // 东帝汶 Timor-Leste
  "678": "STP",   // 圣多美和普林西比 São Tomé and Príncipe
  "688": "SRB",   // 塞尔维亚 Serbia
  "702": "SGP",   // 新加坡 Singapore
  "704": "VNM",   // 越南 Vietnam
  "776": "TON",   // 汤加 Tonga
  "807": "MKD",   // 北马其顿 North Macedonia
  // ── 中国大陆以外的中国地区（视觉上与大陆合并为 CHN）──────────────────────────
  "158": "CHN",   // 台湾 Taiwan
  "344": "CHN",   // 香港 Hong Kong
  "446": "CHN",   // 澳门 Macao
};

/**
 * 将 TopoJSON 国家数字 ID 解析为 ISO3。
 * 兼容 world-atlas 中可能出现的前导零 ID（如 "032"）。
 */
export function getIso3FromNumericId(rawId: string | number | null | undefined): string {
  if (rawId == null) return "";
  const id = String(rawId).trim();
  if (!id) return "";

  // 优先直接匹配（兼容未补零写法）
  const direct = NUMERIC_TO_ISO3[id];
  if (direct) return direct;

  // 再尝试去掉前导零后的数字键（如 "032" -> "32"）
  const normalized = String(Number.parseInt(id, 10));
  if (normalized && normalized !== "NaN") {
    return NUMERIC_TO_ISO3[normalized] ?? "";
  }
  return "";
}
