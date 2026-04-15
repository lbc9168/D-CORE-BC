import { FileText } from "lucide-react";

export default function ReportsPage() {
  return (
    <div className="flex-1 flex items-center justify-center bg-slate-50">
      <div className="card p-10 flex flex-col items-center gap-4 max-w-md text-center">
        <FileText size={40} className="text-brand-300" />
        <h2 className="text-lg font-semibold text-slate-700">Research Reports</h2>
        <p className="text-sm text-slate-400">
          In-depth research reports and policy analysis coming soon.
        </p>
      </div>
    </div>
  );
}
