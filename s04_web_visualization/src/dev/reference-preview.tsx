/** Development-only acceptance page, deliberately absent from the production entry. */
import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { LanguageProvider, useLang } from "@/contexts/LanguageContext";
import ProjectDetail from "@/components/projects/ProjectDetail";
import type { Project } from "@/types";
import "../index.css";

type Sample = { project: Project; run_id: string; results: { title: string; decision: string; reason: string }[] };
const labels: Record<string, string> = { event_reference: "事件参考", project_reference: "项目参考", rejected: "拒绝", pending: "待确认" };
const reasons: Record<string, string> = { event_evidence_not_located: "模型提供的阶段证据省略了原句内容，无法在正文中精确定位；保持隐藏。", project_evidence_not_located: "项目对应证据无法在正文中定位；保持隐藏。", network_temporarily_unavailable: "页面暂时无法访问，等待后续复核。" };

/** Compare candidate decisions with the real detail component and its empty state. */
function Preview() {
  const [sample, setSample] = useState<Sample | null>(null);
  const [error, setError] = useState("");
  const [empty, setEmpty] = useState(false);
  const { lang, setLang } = useLang();
  useEffect(() => {
    fetch("/src/dev/reference-sample.json").then(async response => {
      if (!response.ok) throw new Error("请先按 s02e README 生成本地样例");
      const data = await response.json();
      if (!data?.project?.project_id || !Array.isArray(data.results)) throw new Error("样例格式不正确");
      setSample(data);
    }).catch((reason: Error) => setError(reason.message));
  }, []);
  if (error) return <p className="p-8">{error}</p>;
  if (!sample) return <p className="p-8">加载本地样例…</p>;
  return <main className="min-h-screen bg-slate-100 p-6">
    <div className="max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold">参考链接 · 单项目验收</h1>
      <p className="text-sm text-slate-500 mt-2">{sample.project.project_id} · 正文核验结果与详情页展示 · 本地原型</p>
      <div className="flex gap-3 my-5">
        <button className="bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm" onClick={() => setLang(lang === "en" ? "zh" : "en")}>{lang === "en" ? "切换中文" : "Switch to English"}</button>
        <button className="bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm" onClick={() => setEmpty(!empty)}>{empty ? "恢复合格参考" : "模拟无合格链接"}</button>
      </div>
      <div className="grid lg:grid-cols-2 gap-6 items-start">
        <section className="space-y-3">
          <h2 className="font-semibold">候选核验记录</h2>
          {sample.results.map((row, index) => <article key={index} className="bg-white rounded-xl p-4 border border-slate-200">
            <span className={`text-xs font-semibold ${row.decision.endsWith("reference") ? "text-emerald-700" : "text-amber-700"}`}>{labels[row.decision] || row.decision}</span>
            <h3 className="text-sm font-semibold mt-2">{row.title || "页面暂不可读"}</h3>
            <p className="text-sm text-slate-600 mt-2 leading-relaxed">{reasons[row.reason] || row.reason}</p>
          </article>)}
          <p className="text-xs text-slate-500">批次 {sample.run_id}。转载标明原始发布者；参考链接不代表全部项目事实已核实。</p>
          {empty && <p role="status" className="text-sm text-amber-700">当前模拟：全部参考待确认或过期。右侧参考区块应隐藏。</p>}
        </section>
        <aside className="rounded-xl overflow-hidden border border-slate-200 shadow-sm">
          <ProjectDetail project={{ ...sample.project, external_references: empty ? [] : sample.project.external_references }} onClose={() => window.scrollTo({ top: 0, behavior: "smooth" })}/>
        </aside>
      </div>
    </div>
  </main>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><LanguageProvider><Preview/></LanguageProvider></React.StrictMode>);
