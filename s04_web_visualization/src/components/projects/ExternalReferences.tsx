import { ExternalLink } from "lucide-react";
import type { ExternalReference } from "@/types";
import { useLang } from "@/contexts/LanguageContext";
import { filterExternalReferences } from "@/utils/externalReferences";

/** Shared reading section; legacy link arrays are deliberately not accepted. */
export default function ExternalReferences({ references }: { references: unknown }) {
  const { lang, t } = useLang();
  const valid = filterExternalReferences(references);
  if (!valid.length) return null;

  const events = new Map<string, ExternalReference[]>();
  const project: ExternalReference[] = [];
  for (const reference of valid) {
    if (reference.scope === "project") project.push(reference);
    else {
      const key = reference.event_id!;
      events.set(key, [...(events.get(key) ?? []), reference]);
    }
  }

  function readings(items: ExternalReference[]) {
    return (
      <ul className="divide-y divide-slate-200/70">
        {items.map(reference => (
          <li key={reference.url} className="py-2.5 first:pt-0 last:pb-0">
            <a href={reference.url} target="_blank" rel="noopener noreferrer"
              className="inline-flex max-w-full items-start gap-1.5 text-xs font-medium text-brand-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
              <span className="break-words [overflow-wrap:anywhere]">{reference.title}</span>
              <ExternalLink size={12} className="mt-0.5 flex-shrink-0" aria-hidden="true" />
            </a>
            <div className="mt-1 text-[11px] leading-relaxed text-slate-500 break-words [overflow-wrap:anywhere]">
              {reference.publisher} · {t(`externalReferences.${reference.source_type}`)}
              {reference.is_reprint && (
                <span> · {reference.original_publisher
                  ? t("externalReferences.reprintedFrom", { publisher: reference.original_publisher })
                  : t("externalReferences.reprint")}</span>
              )}
            </div>
            <div className="mt-0.5 text-[11px] text-slate-400">
              {t("externalReferences.lastChecked")} {reference.checked_at.slice(0, 10)}
            </div>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section>
      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
        {t("externalReferences.title")}
      </h4>
      <div className="bg-slate-50 rounded-xl p-3 space-y-4">
        {[...events.entries()].map(([eventId, items]) => {
          const event = items[0];
          const stage = lang === "en" ? event.stage_en || event.stage : event.stage;
          return (
            <div key={eventId}>
              <h5 className="text-xs font-semibold text-slate-600 mb-2 break-words">
                {t("externalReferences.event")} · {stage}
              </h5>
              {readings(items)}
            </div>
          );
        })}
        {project.length > 0 && (
          <div>
            <h5 className="text-xs font-semibold text-slate-600 mb-2">{t("externalReferences.project")}</h5>
            {readings(project)}
          </div>
        )}
      </div>
    </section>
  );
}
