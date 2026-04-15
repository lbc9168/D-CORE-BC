import { Info } from "lucide-react";

export default function AboutPage() {
  return (
    <div className="flex-1 flex items-center justify-center bg-slate-50 h-full">
      <div className="card p-10 flex flex-col items-center gap-4 max-w-lg text-center">
        <Info size={40} className="text-brand-300" />
        <h2 className="text-lg font-semibold text-slate-700">About This Database</h2>
        <p className="text-sm text-slate-500 leading-relaxed">
          The BRI Renewable Energy Database tracks Chinese energy investments
          along the Belt and Road Initiative. Data is compiled from public
          sources and validated through cross-verification.
        </p>
        <p className="text-xs text-slate-400 mt-2">Developer information placeholder.</p>
      </div>
    </div>
  );
}
