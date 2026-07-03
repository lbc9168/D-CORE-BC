export interface Update {
  id: string;
  date: string;      // YYYY-MM-DD
  body_en: string;   // 200–300 word note (English)
}

/**
 * 更新动态：每次更新在数组顶部新增一条即可（页面按 date 倒序展示）。
 * body_en 支持用空行分段（渲染时保留换行）。
 */
export const updates: Update[] = [
  {
    id: "2026-07-03",
    date: "2026-07-03",
    body_en:
      "Welcome to the D-CORE updates feed. This is where we post a short note each time the database is refreshed or the website changes, so you can see at a glance what is new.\n\n" +
      "In this update, the project database was refreshed with the latest milestone data through mid-2026, adding newly reported signings, construction starts, and completions across the markets we track. The Overview dashboard's annual trend chart now lets you switch between project count and installed capacity, and drill into a single year to see how activity breaks down by energy type. We also clarified the three core concepts used throughout the site — projects, milestones, and events — in the note at the top of the Overview page.\n\n" +
      "On the data side, the downloadable CSV on the Data Table page now comes with an Excel data dictionary describing every column, making the event-level export easier to work with. Chart category colors were unified with the rest of the site for a cleaner, more consistent look, and countries with no tracked projects now appear in a neutral gray on the map.\n\n" +
      "As always, the database is compiled from public sources and validated through cross-verification. While we work hard to keep it accurate, errors and omissions are possible — please use your own judgment when relying on the data, and feel free to reach out with corrections or questions. Thank you for following the project.",
  },
];
