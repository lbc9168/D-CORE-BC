import { lazy } from "react";
import { HashRouter, Routes, Route } from "react-router-dom";
import { LanguageProvider } from "@/contexts/LanguageContext";
import { DataProvider } from "@/contexts/DataContext";
import Layout from "@/components/layout/Layout";
import MapPage from "@/pages/MapPage";

// 首页（地图）同步加载；其余页面按路由懒加载，把 recharts 等重库延后到访问对应页时再载
const OverviewPage = lazy(() => import("@/pages/OverviewPage"));
const TablePage = lazy(() => import("@/pages/TablePage"));
const ReportsPage = lazy(() => import("@/pages/ReportsPage"));
const AboutPage = lazy(() => import("@/pages/AboutPage"));

export default function App() {
  return (
    <LanguageProvider>
      <DataProvider>
        <HashRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<MapPage />} />
              <Route path="overview" element={<OverviewPage />} />
              <Route path="table" element={<TablePage />} />
              <Route path="reports" element={<ReportsPage />} />
              <Route path="about" element={<AboutPage />} />
            </Route>
          </Routes>
        </HashRouter>
      </DataProvider>
    </LanguageProvider>
  );
}
