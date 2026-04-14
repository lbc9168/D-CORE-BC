import { NavLink } from "react-router-dom";
import { Globe, Table2 } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";

export default function Header() {
  const { t } = useLang();

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
      isActive
        ? "bg-white/20 text-white"
        : "text-white/75 hover:bg-white/10 hover:text-white"
    }`;

  return (
    <header className="h-14 bg-brand-500 border-b border-brand-600 flex items-center px-4 gap-4 shadow-md flex-shrink-0">
      {/* 标题 */}
      <div className="flex items-center gap-2 mr-4">
        <span className="text-white text-xl">⚡</span>
        <span className="font-semibold text-white text-sm leading-tight hidden sm:block">
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
