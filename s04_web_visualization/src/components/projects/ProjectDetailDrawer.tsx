import { useEffect, useRef } from "react";
import type { Project } from "@/types";
import ProjectDetail from "./ProjectDetail";

/**
 * 项目详情滑出抽屉：固定在视口右缘的覆盖图层（不挤占页面布局），
 * 半透明遮罩点击或 Esc 关闭；project=null 时滑出收起。
 */
export default function ProjectDetailDrawer({
  project,
  onClose,
}: {
  project: Project | null;
  onClose: () => void;
}) {
  const open = project != null;
  // 关闭动画期间保留最后内容，避免滑出途中内容闪空
  const lastRef = useRef<Project | null>(null);
  if (project) lastRef.current = project;
  const shown = project ?? lastRef.current;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {/* 遮罩 */}
      <div
        className={`fixed inset-0 z-40 bg-slate-900/20 transition-opacity duration-300 ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
        aria-hidden
      />
      {/* 抽屉面板：关闭后 invisible（visibility 在 transform 动画结束才翻转），避免离屏内容残留焦点序列 */}
      <aside
        className={`fixed inset-y-0 right-0 z-50 w-[26rem] max-w-[92vw] bg-white shadow-2xl
                    border-l border-slate-200 transition-[transform,visibility] duration-300 ease-in-out ${
                      open ? "translate-x-0 visible" : "translate-x-full invisible"
                    }`}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
      >
        {shown && (
          <ProjectDetail key={shown.project_id} project={shown} onClose={onClose} />
        )}
      </aside>
    </>
  );
}
