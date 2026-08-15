import { useLang } from "@/contexts/LanguageContext";

export type TxMfKind = "all" | "tx" | "mf";

/** TX/MF 子域切换胶囊（样式对齐 EntityChart 的模式切换钮），Overview 产业链侧图表级筛选用 */
export default function KindToggle({
  value,
  onChange,
}: {
  value: TxMfKind;
  onChange: (v: TxMfKind) => void;
}) {
  const { t } = useLang();
  const opts: { key: TxMfKind; labelKey: string }[] = [
    { key: "all", labelKey: "filter.txmfAll" },
    { key: "tx", labelKey: "filter.txmfTx" },
    { key: "mf", labelKey: "filter.txmfMf" },
  ];
  return (
    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 w-fit">
      {opts.map((o) => (
        <button
          key={o.key}
          className={`px-2 py-1 text-[11px] rounded-md ${
            value === o.key ? "bg-brand-600 text-white" : "text-slate-600"
          }`}
          onClick={() => onChange(o.key)}
        >
          {t(o.labelKey)}
        </button>
      ))}
    </div>
  );
}
