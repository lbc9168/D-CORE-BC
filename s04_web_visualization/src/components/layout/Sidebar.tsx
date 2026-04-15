import { NavLink } from "react-router-dom";
import { Globe, Table2, FileText, Info } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";

export default function Sidebar() {
  const { t } = useLang();

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
      isActive
        ? "bg-white/20 text-white"
        : "text-white/70 hover:bg-white/10 hover:text-white"
    }`;

  return (
    <aside className="w-[13rem] min-w-[180px] h-full bg-brand-700 flex flex-col flex-shrink-0">
      {/* Logo / Title */}
      <div className="px-4 py-4 border-b border-white/10">
        <div className="font-semibold text-white text-base leading-snug">
          {t("nav.title")}
        </div>
      </div>

      {/* Nav items */}
      <nav className="flex flex-col gap-1 p-2 flex-1">
        <NavLink to="/" end className={linkClass}>
          <Globe size={16} />
          {t("nav.home")}
        </NavLink>
        <NavLink to="/table" className={linkClass}>
          <Table2 size={16} />
          {t("nav.table")}
        </NavLink>
        <NavLink to="/reports" className={linkClass}>
          <FileText size={16} />
          {t("nav.reports")}
        </NavLink>
        <NavLink to="/about" className={linkClass}>
          <Info size={16} />
          {t("nav.about")}
        </NavLink>
      </nav>
    </aside>
  );
}
