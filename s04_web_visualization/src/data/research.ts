/** External publications using D-CORE. Link to the publisher's original page. */
export interface ResearchPublication {
  id: string;
  title: string;
  authors: string;
  publisher: string;
  date: string;
  url: string;
  description_en: string;
  description_zh: string;
  cover?: string;
}

export const researchPublications: ResearchPublication[] = [
  {
    id: "crea-pacs-five-years-2026",
    title: "Five years on: China seeds overseas renewables growth but coal ban gaps persist",
    authors: "Lizzie Frost, Katherine Hasan, Syahdiva Moezbar and Xiaojun Wang",
    publisher: "Centre for Research on Energy and Clean Air (CREA) · PACS",
    // Landing-page date; the linked PDF carries 24 September 2026.
    date: "2026-09-23",
    // Thumbnail rendered from page 1 of the publisher's original PDF.
    cover: "covers/crea-pacs-five-years-2026.webp",
    url: "https://energyandcleanair.org/publication/five-years-on-china-seeds-overseas-renewables-growth-but-coal-ban-gaps-persist/",
    description_en: "A five-year review of China's overseas coal pledge, its remaining gaps and the growth of Chinese-linked renewables, with recommendations for accelerating the transition to clean energy.",
    description_zh: "回顾中国海外煤电承诺五年来的进展、仍存的缺口及中国企业参与的海外可再生能源发展，并提出加快清洁能源转型的建议。",
  },
];
