import React, { createContext, useContext, useState, useCallback } from "react";
import type { Lang } from "@/types";
import { translations, interpolate } from "@/i18n";
import type { Translations } from "@/i18n";

interface LanguageContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (path: string, values?: Record<string, string | number>) => string;
  T: Translations;   // 直接访问翻译对象（方便解构）
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

/** 递归读取嵌套翻译 key，例如 "filter.title" */
function getNestedValue(obj: Record<string, unknown>, path: string): string {
  const keys = path.split(".");
  let current: unknown = obj;
  for (const key of keys) {
    if (current == null || typeof current !== "object") return path;
    current = (current as Record<string, unknown>)[key];
  }
  return typeof current === "string" ? current : path;
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = useState<Lang>("zh");

  const t = useCallback(
    (path: string, values?: Record<string, string | number>): string => {
      const T = translations[lang] as unknown as Record<string, unknown>;
      const raw = getNestedValue(T, path);
      return values ? interpolate(raw, values) : raw;
    },
    [lang],
  );

  return (
    <LanguageContext.Provider value={{ lang, setLang, t, T: translations[lang] }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLang(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLang must be used within LanguageProvider");
  return ctx;
}
