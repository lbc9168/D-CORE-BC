import { NavLink } from "react-router-dom";
import { Globe, Table2, Languages } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";

export default function Header() {
  const { lang, setLang, t } = useLang();

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
      isActive
        ? "bg-brand-600 text-white"
        : "text-slate-600 hover:bg-slate-100"
    }`;

  return (
    <header className="h-14 bg-white border-b border-slate-200 flex items-center px-4 gap-4 shadow-sm flex-shrink-0">
      {/* 标题 */}
      <div className="flex items-center gap-2 mr-4">
        <span className="text-brand-600 text-xl">⚡</span>
        <span className="font-semibold text-slate-800 text-sm leading-tight hidden sm:block">
          {t("nav.title")}
        </span>
      </div>

      {/* 导航 */}
      <nav className="flex items-center gap-1">
        <NavLink to="/" end className={navClass}>
          <Globe size={15} />
          {t("nav.map")}
        </NavLink>
        <NavLink to="/table" className={navClass}>
          <Table2 size={15} />
          {t("nav.table")}
        </NavLink>
      </nav>

      {/* 右侧：语言切换 */}
      <div className="ml-auto flex items-center gap-2">
        <Languages size={15} className="text-slate-400" />
        <button
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          className="btn-outline text-xs"
        >
          {lang === "zh" ? "EN" : "中文"}
        </button>
      </div>
    </header>
  );
}
