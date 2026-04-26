import { FileText, ExternalLink } from "lucide-react";
import { useLang } from "@/contexts/LanguageContext";
import { reports } from "@/data/reports";

/** Reports 页面：展示研究报告卡片列表，PDF 文件存放于 public/reports/ */
export default function ReportsPage() {
  const { T, lang } = useLang();

  if (reports.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center bg-slate-50">
        <div className="card p-10 flex flex-col items-center gap-4 max-w-md text-center">
          <FileText size={40} className="text-brand-300" />
          <h2 className="text-lg font-semibold text-slate-700">{T.reports.title}</h2>
          <p className="text-sm text-slate-400">{T.reports.empty}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-8">
      <h2 className="text-xl font-semibold text-slate-700 mb-6">{T.reports.title}</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {reports.map((report) => (
          <div key={report.id} className="card p-6 flex flex-col gap-3">
            <div className="flex items-start gap-3">
              <FileText size={20} className="text-brand-400 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-slate-700 leading-snug">
                  {lang === "zh" ? report.title_zh : report.title_en}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">{report.date}</p>
              </div>
            </div>
            <p className="text-sm text-slate-500 leading-relaxed">
              {lang === "zh" ? report.description_zh : report.description_en}
            </p>
            <a
              href={`/reports/${report.filename}`}
              target="_blank"
              rel="noopener noreferrer"
              className="self-start inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700 transition-colors"
            >
              {T.reports.open}
              <ExternalLink size={13} />
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}
