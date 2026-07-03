import { Suspense } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* 懒加载页面 chunk 载入期间占位；包在 Outlet 外，侧栏保持可见 */}
        <Suspense fallback={<PageFallback />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}

/** 路由懒加载占位：居中转圈 */
function PageFallback() {
  return (
    <div className="flex flex-1 items-center justify-center text-slate-400">
      <div className="w-8 h-8 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}
