import { useState, useEffect } from "react";

type GeoStatus = "idle" | "loading" | "ok" | "unavailable" | "error";

interface GeoState {
  status: GeoStatus;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any | null;
}

const cache = new Map<string, GeoState>();

/**
 * 按需加载省级 GeoJSON。
 * 文件路径：/geo/provinces/{ISO3}.geojson（由 download_geo.py 生成）
 */
export function useProvinceGeo(iso3: string | null): GeoState {
  const [state, setState] = useState<GeoState>(
    iso3 ? (cache.get(iso3) ?? { status: "idle", data: null }) : { status: "idle", data: null },
  );

  useEffect(() => {
    if (!iso3) {
      setState({ status: "idle", data: null });
      return;
    }

    const cached = cache.get(iso3);
    if (cached) {
      setState(cached);
      return;
    }

    setState({ status: "loading", data: null });

    fetch(`${import.meta.env.BASE_URL}geo/provinces/${iso3}.geojson`)
      .then(async (res) => {
        if (res.status === 404) {
          const s: GeoState = { status: "unavailable", data: null };
          cache.set(iso3, s);
          setState(s);
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const s: GeoState = { status: "ok", data };
        cache.set(iso3, s);
        setState(s);
      })
      .catch(() => {
        const s: GeoState = { status: "unavailable", data: null };
        cache.set(iso3, s);
        setState(s);
      });
  }, [iso3]);

  return state;
}
