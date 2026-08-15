import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import type { DomainKey, MetricKey } from "@/types";
import { DOMAIN_META, defaultMetric, isDomainKey } from "@/utils/domains";
import { useData } from "@/contexts/DataContext";

/**
 * DomainContext —— Map 页"当前看哪个数据域 + 用什么度量"的状态层。
 *
 * 域状态持久化在 URL query（?domain=storage，可分享/前进后退）；度量是域的属性，
 * 存本地 state，切域时重置为该域默认。切域会清空跨域不再合法的筛选字段
 * （categories/subcategories/groups/txmfKind），保留三域通用维度（年份/地区/BRI/LDC/SOE）。
 */

interface DomainContextValue {
  domain: DomainKey;
  setDomain: (d: DomainKey) => void;
  metric: MetricKey;
  setMetric: (m: MetricKey) => void;
}

const DomainContext = createContext<DomainContextValue | null>(null);

export function DomainProvider({ children }: { children: React.ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const { setFilter, ensureTxMfLoaded } = useData();

  const raw = searchParams.get("domain");
  // 旧链接兼容：Phase B 曾有独立储能域（?domain=storage），现映射为发电域+储能透镜度量
  const legacyStorage = raw === "storage";
  const domain: DomainKey = isDomainKey(raw) ? raw : "renewable";

  const [metric, setMetricState] = useState<MetricKey>(() =>
    legacyStorage ? "storage_energy_mwh" : defaultMetric(domain),
  );

  // 兜底：URL 直达 / 浏览器前进后退改变 domain 时，校正度量与 tx/mf 数据加载
  useEffect(() => {
    setMetricState((m) => (DOMAIN_META[domain].metrics.includes(m) ? m : defaultMetric(domain)));
    if (domain === "txmf") ensureTxMfLoaded();
  }, [domain, ensureTxMfLoaded]);

  const setDomain = useCallback(
    (d: DomainKey) => {
      if (d === domain) return;
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (d === "renewable") next.delete("domain");  // 默认域不留参数，URL 干净
          else next.set("domain", d);
          return next;
        },
        { replace: true },
      );
      setMetricState(defaultMetric(d));
      // 清空跨域不再合法的筛选；通用维度保留
      setFilter((f) => ({ ...f, categories: [], subcategories: [], groups: [], txmfKind: "all" }));
      if (d === "txmf") ensureTxMfLoaded();
    },
    [domain, setSearchParams, setFilter, ensureTxMfLoaded],
  );

  const setMetric = useCallback(
    (m: MetricKey) => {
      if (DOMAIN_META[domain].metrics.includes(m)) setMetricState(m);
    },
    [domain],
  );

  return (
    <DomainContext.Provider value={{ domain, setDomain, metric, setMetric }}>
      {children}
    </DomainContext.Provider>
  );
}

export function useDomain(): DomainContextValue {
  const ctx = useContext(DomainContext);
  if (!ctx) throw new Error("useDomain must be used within DomainProvider");
  return ctx;
}
