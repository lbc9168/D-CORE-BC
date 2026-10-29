export interface Report {
  id: string;
  title_zh: string;
  title_en: string;
  date: string;
  description_zh: string;
  description_en: string;
  filename: string;
  period: string;
  cover: string;
}

/** 将 PDF 文件放入 public/reports/ 后，在此数组添加对应元数据即可 */
export const reports: Report[] = [
  {
    id: "report-04",
    title_zh: "中国海外可再生能源项目全景报告（2026年第三季度）",
    title_en: "The Landscape of Chinese Overseas Renewable Energy Projects — Q3 2026",
    date: "2026-10",
    description_zh: "回顾2026年第三季度中国企业海外可再生能源项目进展，涵盖发电与储能、输变电和新能源制造业。",
    description_en: "A review of Chinese overseas renewable energy project developments in Q3 2026, covering generation and storage, transmission, and clean energy manufacturing.",
    filename: "D-CORE_Report_Q3_2026.pdf",
    period: "Q3 2026",
    cover: "covers/dcore-q3-2026.svg",
  },
  {
    id: "report-03",
    title_zh: "中国海外可再生能源投资全景报告（2026年第二季度）",
    title_en: "The Landscape of Chinese Renewable Energy Projects Overseas — Q2 2026",
    date: "2026-07",
    description_zh: "2026年第二季度中国企业海外可再生能源投资动态与项目进展。",
    description_en: "Quarterly update on Chinese overseas renewable energy investment activity and project developments in Q2 2026.",
    filename: "D-CORE_Q2_2026.pdf",
    period: "Q2 2026",
    cover: "covers/dcore-q2-2026.svg",
  },
  {
    id: "report-02",
    title_zh: "中国海外可再生能源投资全景报告（2026年第一季度）",
    title_en: "The Landscape of Chinese Renewable Energy Projects Overseas — Q1 2026",
    date: "2026-04",
    description_zh: "2026年第一季度中国企业海外可再生能源投资动态与项目进展。",
    description_en: "Quarterly update on Chinese overseas renewable energy investment activity and project developments in Q1 2026.",
    filename: "D-CORE_Q1_2026.pdf",
    period: "Q1 2026",
    cover: "covers/dcore-q1-2026.svg",
  },
  {
    id: "report-01",
    title_zh: "中国海外可再生能源投资全景报告（2022—2025）",
    title_en: "The Landscape of Chinese Renewable Energy Projects Overseas (2022 to 2025)",
    date: "2026-01",
    description_zh: "全面梳理2022至2025年间中国企业参与海外可再生能源项目的投资格局与趋势。",
    description_en: "A comprehensive overview of Chinese overseas renewable energy investment patterns and trends from 2022 to 2025.",
    filename: "D-CORE_2022to2025.pdf",
    period: "2022–2025",
    cover: "covers/dcore-2022-2025.svg",
  },
  
];
