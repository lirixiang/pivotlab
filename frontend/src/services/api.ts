import type {
  MarketOverview,
  ScreenerResponse,
  StockDetail,
  WatchlistItem,
} from "../types";

const BASE = "/api";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const api = {
  market: () => http<MarketOverview>("/market/overview"),
  universe: () =>
    http<{ code: string; name: string; industry: string }[]>("/stocks/universe"),
  stock: (code: string, opts?: { period?: string; lookback?: number; sensitivity?: number }) => {
    const q = new URLSearchParams();
    if (opts?.period) q.set("period", opts.period);
    if (opts?.lookback) q.set("lookback", String(opts.lookback));
    if (opts?.sensitivity) q.set("sensitivity", String(opts.sensitivity));
    const qs = q.toString();
    return http<StockDetail>(`/stocks/${code}${qs ? "?" + qs : ""}`);
  },
  screener: (pattern: string, limit = 50) =>
    http<ScreenerResponse>(`/screener/${pattern}?limit=${limit}`),
  watchlist: () => http<WatchlistItem[]>("/watchlist"),
  addWatch: (code: string, name = "") =>
    http<WatchlistItem>("/watchlist", {
      method: "POST",
      body: JSON.stringify({ code, name }),
    }),
  removeWatch: (code: string) =>
    http<{ ok: boolean }>(`/watchlist/${code}`, { method: "DELETE" }),
};
