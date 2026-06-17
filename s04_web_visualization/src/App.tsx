import { HashRouter, Routes, Route } from "react-router-dom";
import { LanguageProvider } from "@/contexts/LanguageContext";
import { DataProvider } from "@/contexts/DataContext";
import Layout from "@/components/layout/Layout";
import MapPage from "@/pages/MapPage";
import OverviewPage from "@/pages/OverviewPage";
import TablePage from "@/pages/TablePage";
import ReportsPage from "@/pages/ReportsPage";
import AboutPage from "@/pages/AboutPage";

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
