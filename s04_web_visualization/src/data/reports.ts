export interface Report {
  id: string;
  title_zh: string;
  title_en: string;
  date: string;
  description_zh: string;
  description_en: string;
  filename: string;
}

/** 将 PDF 文件放入 public/reports/ 后，在此数组添加对应元数据即可 */
export const reports: Report[] = [];
