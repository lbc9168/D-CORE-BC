import type { ExternalReference } from "@/types";

const MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000;
const hasText = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;
const nullableText = (value: unknown): value is string | null =>
  value === null || hasText(value);

function validCalendarDate(value: string): boolean {
  const [year, month = 1, day = 1] = value.split("-").map(Number);
  if (year < 1000 || month < 1 || month > 12 || day < 1) return false;
  return day <= new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function timestamp(value: unknown): number {
  if (typeof value !== "string" ||
      !/^\d{4}-\d{2}-\d{2}(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?$/.test(value) ||
      !validCalendarDate(value.slice(0, 10))) return NaN;
  return Date.parse(value);
}

/** A display boundary, not a network check: only ordinary public-shaped DNS URLs. */
function publicUrl(value: unknown): string | null {
  if (!hasText(value) || value !== value.trim() || /[\s\\\u0000-\u001f\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password) return null;
    const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
    if (hostname === "goalfore.cn" || hostname.endsWith(".goalfore.cn")) return null;
    const labels = hostname.split(".");
    // Reject IP literals (including URL-normalized numeric forms), single-label and local names.
    if (labels.length < 2 || hostname.length > 253 ||
        labels.some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
        !/^[a-z][a-z0-9-]*$/.test(labels[labels.length - 1]) ||
        /(?:^|\.)(?:localhost|local|internal|lan|home|test|invalid|example|onion|arpa)$/.test(hostname)) return null;
    return url.href;
  } catch (error) {
    if (error instanceof TypeError) return null;
    throw error;
  }
}

/** Reject malformed or stale runtime JSON before it reaches either detail panel. */
export function filterExternalReferences(input: unknown, now = Date.now()): ExternalReference[] {
  if (!Array.isArray(input) || !Number.isFinite(now)) return [];
  const accepted: ExternalReference[] = [];
  const seen = new Set<string>();
  for (const value of input) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const r = value as Record<string, unknown>;
    const url = publicUrl(r.url);
    if (!url || !hasText(r.reference_id) || !hasText(r.title) || !hasText(r.publisher) ||
        (r.source_type !== "official" && r.source_type !== "media") ||
        (r.scope !== "event" && r.scope !== "project") ||
        !nullableText(r.event_id) || !nullableText(r.stage) || !nullableText(r.stage_en) ||
        !nullableText(r.original_publisher) || typeof r.is_reprint !== "boolean" ||
        (r.event_date !== null && (typeof r.event_date !== "string" ||
          !/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(r.event_date) || !validCalendarDate(r.event_date))) ||
        (r.scope === "event" && (!hasText(r.event_id) || !hasText(r.stage)))) continue;
    const checked = timestamp(r.checked_at);
    const until = timestamp(r.valid_until);
    if (!Number.isFinite(checked) || !Number.isFinite(until) ||
        checked > now || now >= until || until > checked + MAX_AGE_MS) continue;
    // A shared article may legitimately support multiple events; fragments do not create new readings.
    const canonicalUrl = new URL(url);
    canonicalUrl.hash = "";
    const key = JSON.stringify([r.scope, r.scope === "event" ? r.event_id : null, canonicalUrl.href]);
    if (seen.has(key)) continue;
    seen.add(key);
    accepted.push({ ...r, url } as unknown as ExternalReference);
  }
  return accepted;
}
