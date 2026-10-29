import { Megaphone } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";
import { updates } from "@/data/updates";

/** Updates 页面：每次更新一张微博式卡片，按日期倒序展示；内容在 src/data/updates.ts */
export default function UpdatesPage() {
  const { T } = useLang();
  const sorted = [...updates].sort((a, b) => (a.date < b.date ? 1 : -1));
  // Independent desktop stacks avoid row gaps. On mobile, display:contents
  // and the original index restore one continuous chronological feed.
  const columns = [0, 1].map((column) => sorted
    .map((update, index) => ({ update, index }))
    .filter(({ index }) => index % 2 === column));

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-4 sm:p-8">
      <h2 className="text-2xl font-semibold text-slate-800 mb-6">{T.updates.title}</h2>

      {sorted.length === 0 ? (
        <div className="card p-10 flex flex-col items-center gap-3 max-w-md mx-auto text-center">
          <Megaphone size={40} className="text-brand-300" />
          <p className="text-sm text-slate-400">{T.updates.empty}</p>
        </div>
      ) : (
        <div className="mx-auto flex max-w-6xl flex-col gap-6 md:flex-row md:items-start">
          {columns.map((column, columnIndex) => (
            <div key={columnIndex} className="contents md:flex md:min-w-0 md:flex-1 md:flex-col md:gap-6">
          {column.map(({ update: u, index }) => (
            <article key={u.id} style={{ order: index }} className="card p-6">
              <div className="flex items-center gap-2 mb-1 text-brand-400">
                <Megaphone size={16} className="shrink-0" />
                <time className="text-xs font-medium text-slate-500">{u.date}</time>
              </div>
              <h3 className="text-base font-semibold text-slate-700 mb-2">{u.title_en}</h3>
              <p className="text-sm text-slate-600 leading-relaxed whitespace-pre-line">
                {u.body_en}
              </p>
            </article>
          ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
