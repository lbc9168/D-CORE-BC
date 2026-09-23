import { lazy } from "react";
import { HashRouter, Routes, Route } from "react-router-dom";
import { LanguageProvider } from "@/contexts/LanguageContext";
import { DataProvider } from "@/contexts/DataContext";
import { DomainProvider } from "@/contexts/DomainContext";
import Layout from "@/components/layout/Layout";
import MapPage from "@/pages/MapPage";

// 首页（地图）同步加载；其余页面按路由懒加载，把 recharts 等重库延后到访问对应页时再载
const OverviewPage = lazy(() => import("@/pages/OverviewPage"));
const TablePage = lazy(() => import("@/pages/TablePage"));
const ReportsPage = lazy(() => import("@/pages/ReportsPage"));
const UpdatesPage = lazy(() => import("@/pages/UpdatesPage"));
const AboutPage = lazy(() => import("@/pages/AboutPage"));
const LabPage = lazy(() => import("@/pages/LabPage"));

export default function App() {
  return (
    <LanguageProvider>
      <DataProvider>
        <HashRouter>
          {/* DomainProvider 需要 Router（useSearchParams）与 DataProvider（setFilter/ensureTxMfLoaded） */}
          <DomainProvider>
            <Routes>
              <Route path="/" element={<Layout />}>
                <Route index element={<MapPage />} />
                <Route path="overview" element={<OverviewPage />} />
                <Route path="table" element={<TablePage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="updates" element={<UpdatesPage />} />
                <Route path="about" element={<AboutPage />} />
                <Route path="lab" element={<LabPage />} />
              </Route>
            </Routes>
          </DomainProvider>
        </HashRouter>
      </DataProvider>
    </LanguageProvider>
  );
}
