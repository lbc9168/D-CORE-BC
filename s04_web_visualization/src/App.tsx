import { HashRouter, Routes, Route } from "react-router-dom";
import { LanguageProvider } from "@/contexts/LanguageContext";
import { DataProvider } from "@/contexts/DataContext";
import Header from "@/components/layout/Header";
import MapPage from "@/pages/MapPage";
import TablePage from "@/pages/TablePage";

export default function App() {
  return (
    <LanguageProvider>
      <DataProvider>
        <HashRouter>
          <div className="h-screen flex flex-col overflow-hidden">
            <Header />
            <main className="flex-1 overflow-hidden">
              <Routes>
                <Route path="/" element={<MapPage />} />
                <Route path="/table" element={<TablePage />} />
              </Routes>
            </main>
          </div>
        </HashRouter>
      </DataProvider>
    </LanguageProvider>
  );
}
