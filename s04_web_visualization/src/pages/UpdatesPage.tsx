import { Megaphone } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";
import { updates } from "@/data/updates";

/** Updates 页面：每次更新一张微博式卡片，按日期倒序展示；内容在 src/data/updates.ts */
export default function UpdatesPage() {
  const { T } = useLang();
  const sorted = [...updates].sort((a, b) => (a.date < b.date ? 1 : -1));

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-8">
      <h2 className="text-xl font-semibold text-slate-700 mb-6">{T.updates.title}</h2>

      {sorted.length === 0 ? (
        <div className="card p-10 flex flex-col items-center gap-3 max-w-md mx-auto text-center">
          <Megaphone size={40} className="text-brand-300" />
          <p className="text-sm text-slate-400">{T.updates.empty}</p>
        </div>
      ) : (
        <div className="max-w-2xl mx-auto flex flex-col gap-4">
          {sorted.map((u) => (
            <article key={u.id} className="card p-6">
              <div className="flex items-center gap-2 mb-3 text-brand-400">
                <Megaphone size={16} className="shrink-0" />
                <time className="text-xs font-medium text-slate-500">{u.date}</time>
              </div>
              <p className="text-sm text-slate-600 leading-relaxed whitespace-pre-line">
                {u.body_en}
              </p>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
