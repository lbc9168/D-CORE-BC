import { ExternalLink } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { useLang } from "@/contexts/LanguageContext";
import { reports } from "@/data/reports";
import { researchPublications } from "@/data/research";

// Vite base keeps local assets working on both the main site and Pages mirror.
const reportAsset = (filename: string) => `${import.meta.env.BASE_URL}reports/${filename}`;

/** Shareable views for D-CORE reports and research using the database. */
export default function ReportsPage() {
  const { T, lang } = useLang();
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") === "research" ? "research" : "dcore";
  const tabs = [{ id: "dcore", label: T.reports.dcore }, { id: "research", label: T.reports.research }];
  const selectTab = (id: string) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    next.set("tab", id);
    return next;
  });
  const [latest, ...previousReports] = reports;
  const text = (en: string, zh: string) => lang === "zh" ? zh : en;

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-4 sm:p-8">
      <div className="mx-auto max-w-6xl">
        <div role="tablist" aria-label={T.reports.pageTitle} className="flex gap-4 border-b border-slate-200 sm:gap-8">
          {tabs.map((tab, index) => (
            <button key={tab.id} id={`tab-${tab.id}`} type="button" role="tab"
              aria-selected={activeTab === tab.id} aria-controls={`panel-${tab.id}`}
              tabIndex={activeTab === tab.id ? 0 : -1} onClick={() => selectTab(tab.id)}
              onKeyDown={(event) => {
                if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
                  : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
                selectTab(tabs[next].id);
                document.getElementById(`tab-${tabs[next].id}`)?.focus();
              }}
              className={`-mb-px whitespace-nowrap border-b-[3px] py-3 text-sm font-semibold transition-colors sm:text-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500 ${activeTab === tab.id ? "border-brand-500 text-brand-600" : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700"}`}
            >{tab.label}</button>
          ))}
        </div>
        <section id="panel-dcore" role="tabpanel" aria-labelledby="tab-dcore" hidden={activeTab !== "dcore"} tabIndex={0} className="pt-7">
          {latest ? <>
            <article className="card overflow-hidden p-5 sm:p-7 lg:p-8">
              <div className="flex flex-col gap-7 sm:flex-row lg:gap-10">
                <a href={reportAsset(latest.filename)} target="_blank" rel="noopener noreferrer" aria-label={`${T.reports.read}: ${text(latest.title_en, latest.title_zh)}`} className="mx-auto w-44 shrink-0 sm:mx-0 sm:w-52 lg:w-60">
                  <img src={reportAsset(latest.cover)} alt="" width={480} height={660} className="w-full rounded shadow-sm" />
                </a>
                <div className="flex flex-col items-start justify-center">
                  <span className="rounded bg-brand-100 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-brand-700">{T.reports.latest}</span>
                  <p className="mt-5 text-sm font-semibold text-brand-600">{latest.period}</p>
                  <h2 className="mt-2 max-w-2xl text-2xl font-semibold leading-tight tracking-tight text-slate-800 lg:text-3xl">{text(latest.title_en, latest.title_zh)}</h2>
                  <p className="mt-3 text-sm text-slate-400"><time dateTime={latest.date}>{latest.date}</time> · {T.reports.quarterly}</p>
                  <p className="mt-4 max-w-2xl text-sm leading-relaxed text-slate-500">{text(latest.description_en, latest.description_zh)}</p>
                  <a href={reportAsset(latest.filename)} target="_blank" rel="noopener noreferrer" className="btn-primary mt-6 px-5 py-2.5">{T.reports.read}<ExternalLink size={15} /></a>
                </div>
              </div>
            </article>
            <h2 className="mb-4 mt-8 text-base font-semibold text-slate-700">{T.reports.previous}</h2>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {previousReports.map((report) => (
                <article key={report.id} className="card flex gap-4 p-5">
                  <img src={reportAsset(report.cover)} alt="" width={480} height={660} loading="lazy" className="h-auto w-24 self-start rounded shadow-sm" />
                  <div className="flex min-w-0 flex-1 flex-col items-start">
                    <p className="text-sm font-semibold text-brand-600">{report.period}</p>
                    <h3 className="mt-2 text-sm font-medium leading-snug text-slate-700">{text(report.title_en, report.title_zh)}</h3>
                    <time dateTime={report.date} className="mb-4 mt-2 text-xs text-slate-400">{report.date}</time>
                    <a href={reportAsset(report.filename)} target="_blank" rel="noopener noreferrer" className="mt-auto inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:underline">{T.reports.open}<ExternalLink size={13} /></a>
                  </div>
                </article>
              ))}
            </div>
          </> : <p className="text-sm text-slate-500">{T.reports.empty}</p>}
        </section>
        <section id="panel-research" role="tabpanel" aria-labelledby="tab-research" hidden={activeTab !== "research"} tabIndex={0} className="pt-7">
          <p className="mb-5 max-w-4xl text-sm leading-relaxed text-slate-500">
            {T.reports.researchIntro}{lang === "en" ? " " : ""}
            <a href="mailto:bingcai.liu@outlook.com" className="font-medium text-brand-600 underline underline-offset-2 hover:text-brand-700">{T.reports.researchContact}</a>
            {T.reports.researchInvitation}
          </p>
          <div className="space-y-5">
            {researchPublications.map((publication) => (
              <article key={publication.id} className="card flex flex-col gap-6 p-6 sm:flex-row sm:p-8">
                {publication.cover && (
                  <a href={publication.url} target="_blank" rel="noopener noreferrer" aria-label={`${T.reports.viewPublication}: ${publication.title}`} className="w-40 shrink-0 self-start sm:w-44">
                    <img src={reportAsset(publication.cover)} alt="" width={440} height={622} loading="lazy" className="h-auto w-full rounded shadow-sm" />
                  </a>
                )}
                <div className="min-w-0 flex-1">
                  <span className="badge bg-brand-100 text-brand-700">{T.reports.researchReport}</span>
                  <h2 className="mt-4 max-w-3xl text-xl font-semibold leading-snug text-slate-800 sm:text-2xl"><a href={publication.url} target="_blank" rel="noopener noreferrer" className="hover:text-brand-600">{publication.title}</a></h2>
                  <p className="mt-3 text-sm font-medium text-slate-600">{publication.publisher}</p>
                  <p className="mt-2 text-sm leading-relaxed text-slate-500">{publication.authors}</p>
                  <time dateTime={publication.date} className="mt-2 block text-xs text-slate-400">{publication.date}</time>
                  <p className="mt-5 max-w-3xl text-sm leading-relaxed text-slate-500">{text(publication.description_en, publication.description_zh)}</p>
                  <a href={publication.url} target="_blank" rel="noopener noreferrer" className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-brand-600 hover:underline">{T.reports.viewPublication}<ExternalLink size={14} /></a>
                </div>
              </article>
            ))}
            {researchPublications.length === 0 && <p className="text-sm text-slate-500">{T.reports.researchEmpty}</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
