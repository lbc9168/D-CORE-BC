import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Globe, LayoutDashboard, Table2, FileText, Info, ChevronRight,
} from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";

/** 导航项配置：未来新增页面（如"企业信息"）只需在此数组加一行 { to, icon, labelKey } */
const NAV_ITEMS = [
  { to: "/", end: true, icon: Globe, labelKey: "nav.home" },
  { to: "/overview", end: false, icon: LayoutDashboard, labelKey: "nav.overview" },
  { to: "/table", end: false, icon: Table2, labelKey: "nav.table" },
  { to: "/reports", end: false, icon: FileText, labelKey: "nav.reports" },
  { to: "/about", end: false, icon: Info, labelKey: "nav.about" },
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

/** 左侧导航栏：折叠态仅图标（3.75rem），点击右缘圆钮丝滑展开显示文字（13.5rem） */
export default function Sidebar() {
  const { t } = useLang();
  const [expanded, setExpanded] = useState(() => readStorage());
  const toggle = () => {
    const next = !expanded;
    writeStorage(next);
    setExpanded(next);
  };

  const labelClass = `whitespace-nowrap overflow-hidden transition-all duration-200 ${
    expanded ? "opacity-100 max-w-[9rem] ml-2.5" : "opacity-0 max-w-0 ml-0"
  }`;

  // 方案 B：标题展开态始终用最终宽度 w-[9rem] 排版，绝不在动画中途的窄宽度下重排（消除展开抖动）；
  // 仅淡入 opacity，由 header 的 overflow-hidden 在侧栏变宽时把标题横向"擦出"。
  // flex-shrink-0：窄侧栏阶段标题保持 9rem 被裁剪，而非被压缩重排。收缩态 w-0+nowrap 单行 0 宽、不增高度。
  const titleClass = `flex-shrink-0 overflow-hidden transition-opacity duration-300 ${
    expanded ? "opacity-100 w-[9rem] ml-2.5" : "opacity-0 w-0 ml-0 whitespace-nowrap"
  }`;

  return (
    <aside
      className={`relative h-full bg-brand-700 flex flex-col flex-shrink-0
                  transition-[width] duration-300 ease-in-out ${
                    expanded ? "w-[13.5rem]" : "w-[3.75rem]"
                  }`}
    >
      {/* Logo / 标题 */}
      <div className="flex items-center px-3 py-4 border-b border-white/10 min-h-[3.5rem] overflow-hidden">
        <div className="w-8 h-8 rounded-lg bg-white/15 text-white flex items-center justify-center
                        font-bold text-xs flex-shrink-0 select-none">
          RE
        </div>
        <div className={`font-semibold text-white text-sm leading-snug ${titleClass}`}>
          {t("nav.title")}
        </div>
      </div>

      {/* 展开/收起 切换钮（悬浮在右缘） */}
      <button
        onClick={toggle}
        aria-label={expanded ? "collapse sidebar" : "expand sidebar"}
        aria-expanded={expanded}
        className="absolute -right-3 top-16 z-30 w-6 h-6 rounded-full bg-white border border-slate-200
                   shadow-sm flex items-center justify-center text-slate-500 hover:text-brand-600
                   hover:shadow transition-all"
      >
        <ChevronRight
          size={13}
          className={`transition-transform duration-300 ${expanded ? "rotate-180" : ""}`}
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
            className={({ isActive }) =>
              `flex items-center rounded-lg px-2.5 py-2.5 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-white/20 text-white"
                  : "text-white/70 hover:bg-white/10 hover:text-white"
              }`
            }
          >
            <Icon size={17} className="flex-shrink-0" />
            <span className={labelClass}>{t(labelKey)}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
