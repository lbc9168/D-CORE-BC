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
      "We gave the whole site a major UI refresh for a smoother, friendlier experience while you explore the data. The two big ones: a brand-new Overview tab for a bird's-eye view of the entire database, and a revamped project details view, go dive in and see for yourself! On the map, countries with no data are now greyed out. Each project's developer now shows up. And a new Excel data dictionary comes with the download. Pages load faster, too.\n\n" +
      "Thanks for following along — more to come!",
  },
  {
    id: "2026-launch",
    date: "2026-04-28",
    title_en: "D-CORE is live!",
    body_en:
      "Welcome to D-CORE — the Database of Chinese Overseas Renewable Energy Projects! We built this to make it easy to see where Chinese companies are backing renewable energy around the world. Here's what you can do in this first release:\n\n" +
      "🗺️ Explore the map\n" +
      "An interactive world map shades each country by its number of projects (or total capacity). Click a country to drop into a province-level view, then open any project for the full story.\n\n" +
      "🔎 Filter to what matters\n" +
      "Slice the data by year, energy type, project stage, Belt-and-Road vs. non-BRI, development status (LDC / developing / developed), and whether a Chinese state-owned enterprise is involved.\n\n" +
      "📋 Dig into the details\n" +
      "The Data Table lets you search, sort, and export the full dataset, and the Reports section gathers our research PDFs.\n\n" +
      "That's the starting point — thanks for stopping by, and stay tuned for more!",
  },
];
