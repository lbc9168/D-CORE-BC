import { useState } from "react";
import { X, Download, Loader2 } from "lucide-react";

export interface SurveyData {
  email: string;
  name: string;
  organization: string;
  org_type: string;
  country: string;
  use_case: string;
  wants_updates: "yes" | "no" | "";
}

interface Props {
  /** 下载文件包含的项目总数（全量，与页面筛选无关） */
  rowCount: number;
  onConfirm: (data: SurveyData) => Promise<void>;
  onClose: () => void;
}

const ORG_TYPES = [
  "Academic",
  "Research Institution",
  "Government",
  "Multilateral Organization",
  "NGO",
  "Media",
  "Private Sector",
];

const STORAGE_KEY = "dcore_survey_v1";

/** 检查用户是否已提交过问卷，返回 email 或 null。 */
export function getSavedSurveyEmail(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { email?: string };
    return parsed.email ?? null;
  } catch {
    return null;
  }
}

/** 在 localStorage 中记录已提交的问卷信息。 */
export function saveSurveyRecord(email: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ email, submittedAt: new Date().toISOString() }));
  } catch {
    // localStorage 不可用时静默失败
  }
}

const EMPTY: SurveyData = {
  email: "", name: "", organization: "", org_type: "",
  country: "", use_case: "", wants_updates: "",
};

/** 下载前弹出的用户信息问卷 Modal。 */
export default function DownloadSurveyModal({ rowCount, onConfirm, onClose }: Props) {
  const [form, setForm] = useState<SurveyData>(EMPTY);
  const [errors, setErrors] = useState<Partial<Record<keyof SurveyData, string>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState("");

  const set = (k: keyof SurveyData, v: string) => {
    setForm((prev) => ({ ...prev, [k]: v }));
    setErrors((prev) => ({ ...prev, [k]: undefined }));
  };

  const validate = (): boolean => {
    const errs: Partial<Record<keyof SurveyData, string>> = {};
    if (!form.email.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email))
      errs.email = "Please enter a valid email address.";
    if (!form.name.trim())          errs.name = "Required.";
    if (!form.organization.trim())  errs.organization = "Required.";
    if (!form.org_type)             errs.org_type = "Please select one.";
    if (!form.country.trim())       errs.country = "Required.";
    if (!form.use_case.trim())      errs.use_case = "Required.";
    if (!form.wants_updates)        errs.wants_updates = "Please select one.";
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    setSubmitting(true);
    setServerError("");
    try {
      await onConfirm(form);
    } catch {
      setServerError("Submission failed, but your download will still proceed.");
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = (k: keyof SurveyData) =>
    `w-full px-3 py-2 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-400 transition ${
      errors[k] ? "border-red-400 bg-red-50" : "border-slate-200"
    }`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">

        {/* Header */}
        <div className="sticky top-0 bg-white rounded-t-2xl border-b border-slate-100 px-6 pt-5 pb-4 z-10">
          <button
            onClick={onClose}
            className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 transition"
          >
            <X size={18} />
          </button>
          <div className="flex items-center gap-2 mb-1">
            <Download size={18} className="text-brand-600" />
            <h2 className="text-base font-semibold text-slate-800">Download D-CORE Dataset</h2>
          </div>
          <p className="text-xs text-slate-500">
            {rowCount.toLocaleString()} projects · CSV format
          </p>
          <p className="text-xs text-slate-400 mt-1">
            Please complete this brief survey before downloading. All fields are required.
          </p>
        </div>

        {/* Form */}
        <div className="px-6 py-5 space-y-4">

          {/* Email */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Email <span className="text-red-500">*</span>
            </label>
            <input
              type="email"
              value={form.email}
              onChange={(e) => set("email", e.target.value)}
              placeholder="you@example.com"
              className={inputCls("email")}
            />
            {errors.email && <p className="text-red-500 text-xs mt-1">{errors.email}</p>}
          </div>

          {/* Name */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="Your full name"
              className={inputCls("name")}
            />
            {errors.name && <p className="text-red-500 text-xs mt-1">{errors.name}</p>}
          </div>

          {/* Organization */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Organization <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.organization}
              onChange={(e) => set("organization", e.target.value)}
              placeholder="Your organization or institution"
              className={inputCls("organization")}
            />
            {errors.organization && <p className="text-red-500 text-xs mt-1">{errors.organization}</p>}
          </div>

          {/* Organization Type */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Organization Type <span className="text-red-500">*</span>
            </label>
            <select
              value={form.org_type}
              onChange={(e) => set("org_type", e.target.value)}
              className={inputCls("org_type") + " bg-white"}
            >
              <option value="">Select type…</option>
              {ORG_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            {errors.org_type && <p className="text-red-500 text-xs mt-1">{errors.org_type}</p>}
          </div>

          {/* Country */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Country <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.country}
              onChange={(e) => set("country", e.target.value)}
              placeholder="Your country"
              className={inputCls("country")}
            />
            {errors.country && <p className="text-red-500 text-xs mt-1">{errors.country}</p>}
          </div>

          {/* Use case */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              How are you using the data? <span className="text-red-500">*</span>
            </label>
            <textarea
              value={form.use_case}
              onChange={(e) => set("use_case", e.target.value)}
              placeholder="Briefly describe your intended use…"
              rows={3}
              className={inputCls("use_case") + " resize-none"}
            />
            {errors.use_case && <p className="text-red-500 text-xs mt-1">{errors.use_case}</p>}
          </div>

          {/* Updates */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-2">
              Would you like to receive updates from D-CORE? <span className="text-red-500">*</span>
            </label>
            <div className="flex gap-6">
              {(["yes", "no"] as const).map((val) => (
                <label key={val} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="wants_updates"
                    value={val}
                    checked={form.wants_updates === val}
                    onChange={() => set("wants_updates", val)}
                    className="accent-brand-600"
                  />
                  <span className="text-sm text-slate-700 capitalize">{val === "yes" ? "Yes" : "No"}</span>
                </label>
              ))}
            </div>
            {errors.wants_updates && <p className="text-red-500 text-xs mt-1">{errors.wants_updates}</p>}
          </div>

          {serverError && (
            <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              {serverError}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-white rounded-b-2xl border-t border-slate-100 px-6 py-4 flex justify-end gap-3">
          <button
            onClick={onClose}
            disabled={submitting}
            className="btn-outline text-sm py-2 px-4 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="btn-primary text-sm py-2 px-5 flex items-center gap-2 disabled:opacity-60"
          >
            {submitting ? (
              <><Loader2 size={14} className="animate-spin" /> Submitting…</>
            ) : (
              <><Download size={14} /> Download CSV</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
