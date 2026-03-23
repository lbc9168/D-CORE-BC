import { NavLink } from "react-router-dom";
import { Globe, Table2 } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";

export default function Header() {
  const { t } = useLang();

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
    </header>
  );
}
