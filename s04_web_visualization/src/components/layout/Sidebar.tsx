import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Globe, LayoutDashboard, Table2, FileText, Megaphone, Info, ChevronRight, FlaskConical,
} from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";

/** 导航项配置：未来新增页面（如"企业信息"）只需在此数组加一行 { to, icon, labelKey } */
const NAV_ITEMS = [
  { to: "/", end: true, icon: Globe, labelKey: "nav.home" },
  { to: "/overview", end: false, icon: LayoutDashboard, labelKey: "nav.overview" },
  { to: "/table", end: false, icon: Table2, labelKey: "nav.table" },
  { to: "/reports", end: false, icon: FileText, labelKey: "nav.reports" },
  { to: "/updates", end: false, icon: Megaphone, labelKey: "nav.updates" },
  { to: "/about", end: false, icon: Info, labelKey: "nav.about" },
  { to: "/lab", end: false, icon: FlaskConical, labelKey: "nav.lab" },
] as const;

/** localStorage key，集中管理避免魔法字符串 */
const STORAGE_KEY = "sidebar_expanded";

/** 从 localStorage 读取侧栏展开状态；storage 不可用时降级为收起。 */
function readStorage(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // localStorage 不可用时静默失败
    return false;
  }
}

/** 将侧栏展开状态写入 localStorage；storage 不可用时静默失败。 */
function writeStorage(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    // localStorage 不可用时静默失败
  }
}

/** Fixed text geometry prevents reflow while the sidebar opens or closes. */
export default function Sidebar() {
  const { t } = useLang();
  const [expanded, setExpanded] = useState(() => readStorage());
  const toggle = () => {
    const next = !expanded;
    writeStorage(next);
    setExpanded(next);
  };

  return (
    <aside
      data-expanded={expanded}
      className="sidebar-shell relative h-full bg-brand-700 flex flex-col flex-shrink-0"
    >
      {/* Logo / 标题 */}
      <div className="relative h-[6.5rem] shrink-0 overflow-hidden border-b border-white/10" aria-label={`D-CORE: ${t("nav.title")}`}>
        <img src={`${import.meta.env.BASE_URL}brand/dcore-symbol-inverse.svg`} alt="" width={36} height={36} className="absolute left-3 top-3.5 h-9 w-9" />
        <img src={`${import.meta.env.BASE_URL}brand/dcore-wordmark-inverse.svg`} alt="" width={144} height={28} className="sidebar-reveal absolute left-[3.5rem] top-[1.125rem] h-7 w-36" />
        <div aria-hidden={!expanded} className="sidebar-reveal absolute left-3 top-[3.375rem] w-48 text-[11.5px] font-normal leading-[1.5] text-white/85">
          <span className="block whitespace-nowrap">Database of Chinese Overseas</span>
          <span className="block whitespace-nowrap">Renewable Energy Projects</span>
        </div>
      </div>

      {/* 展开/收起 切换钮（悬浮在右缘） */}
      <button
        onClick={toggle}
        aria-label={expanded ? "collapse sidebar" : "expand sidebar"}
        aria-expanded={expanded}
        className="absolute -right-3 top-[4.5rem] z-30 w-6 h-6 rounded-full bg-white border border-slate-200
                   shadow-sm flex items-center justify-center text-slate-500 hover:text-brand-600
                   hover:shadow transition-all"
      >
        <ChevronRight
          size={13}
          className={`transition-transform duration-300 motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* 导航项 */}
      <nav className="flex flex-col gap-1 p-2 flex-1 overflow-x-hidden">
        {NAV_ITEMS.map(({ to, end, icon: Icon, labelKey }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            title={expanded ? undefined : t(labelKey)}
            aria-label={t(labelKey)}
            className={({ isActive }) =>
              `flex items-center rounded-lg px-2.5 py-2.5 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-white/20 text-white"
                  : "text-white/70 hover:bg-white/10 hover:text-white"
              }`
            }
          >
            <Icon size={17} className="flex-shrink-0" />
            <span aria-hidden="true" className="sidebar-reveal ml-2.5 w-40 shrink-0 whitespace-nowrap">{t(labelKey)}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
