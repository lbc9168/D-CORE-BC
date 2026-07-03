export interface Update {
  id: string;
  date: string;      // YYYY-MM-DD
  title_en: string;  // short headline (English)
  body_en: string;   // ~200 word note (English); blank lines separate paragraphs
}

/**
 * 更新动态：每次更新在数组顶部新增一条即可（页面按 date 倒序展示）。
 * body_en 用空行分段（渲染时保留换行）。
 */
export const updates: Update[] = [
  {
    id: "2026-q2",
    date: "2026-07-03",
    title_en: "2026 Q2 Update",
    body_en:
      "We've been busy this quarter — here's the big stuff:\n\n" +
      "📊 Fresh data\n" +
      "The database is now current through the end of Q2 2026 (April 1 – June 30), with new signings, construction starts, and completions across every market we track. We also rebuilt the dataset from the ground up and re-checked entries against their original sources for a real accuracy boost.\n\n" +
      "🧠 Smarter extraction\n" +
      "We overhauled how projects get identified and cleaned: better filtering to keep out non-renewable and non-power items, more accurate company names, smarter duplicate-merging, and normalized locations — so a project no longer shows up twice or in the wrong place.\n\n" +
      "✨ On the site\n" +
      "The Overview trend chart now switches between project count and capacity and can break a single year down by energy type; the map greys out countries with no data; project details, the table, and the CSV all show each project's developer now; and a new Excel data dictionary ships with the download. Pages load noticeably faster, too.\n\n" +
      "Thanks for following along — more to come!",
  },
];
