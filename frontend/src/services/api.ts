import type {
  DbStats,
  MarketOverview,
  ScreenerResponse,
  StockDetail,
  SyncTask,
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
  searchStocks: (q: string, limit = 15) =>
    http<{ code: string; name: string; industry: string; market: string }[]>(
      `/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  stock: (code: string, opts?: {
    period?: string; lookback?: number; sensitivity?: number;
    algorithm?: string; factor_weights?: Record<string, number>;
  }) => {
    const q = new URLSearchParams();
    if (opts?.period) q.set("period", opts.period);
    if (opts?.lookback) q.set("lookback", String(opts.lookback));
    if (opts?.sensitivity) q.set("sensitivity", String(opts.sensitivity));
    if (opts?.algorithm) q.set("algorithm", opts.algorithm);
    if (opts?.factor_weights) q.set("factor_weights", JSON.stringify(opts.factor_weights));
    const qs = q.toString();
    return http<StockDetail>(`/stocks/${code}${qs ? "?" + qs : ""}`);
  },
  srFactors: () =>
    http<import("../types").SrFactor[]>("/stocks/meta/sr-factors"),
  screener: (pattern: string, limit = 50) =>
    http<ScreenerResponse>(`/screener/${pattern}?limit=${limit}`),
  triggerScan: () =>
    http<{ status: string; message: string }>("/screener/scan", { method: "POST" }),
  watchlist: () => http<WatchlistItem[]>("/watchlist"),
  watchlistScores: () =>
    http<import("../types").WatchlistScore[]>("/watchlist/scores"),
  addWatch: (code: string, name = "") =>
    http<{ id: number; code: string; name: string; ok: boolean }>("/watchlist", {
      method: "POST",
      body: JSON.stringify({ code, name }),
    }),
  removeWatch: (code: string) =>
    http<{ ok: boolean }>(`/watchlist/${code}`, { method: "DELETE" }),
  // Settings
  getSetting: (key: string) =>
    http<{ key: string; value: Record<string, unknown> }>(`/settings/${key}`),
  putSetting: (key: string, value: Record<string, unknown>) =>
    http<{ ok: boolean }>(`/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  refreshCandles: (code: string, mode: "latest" | "full" = "latest") =>
    http<{ code: string; mode: string; updated_count: number }>(
      `/stocks/${code}/refresh-candles`,
      { method: "POST", body: JSON.stringify({ mode }) },
    ),
  // Sync endpoints
  syncStocks: () => http<{ task_id: number }>("/sync/stocks", { method: "POST" }),
  syncQuotes: () => http<{ task_id: number }>("/sync/quotes", { method: "POST" }),
  syncFinancials: () => http<{ task_id: number }>("/sync/financials", { method: "POST" }),
  syncConcepts: () => http<{ task_id: number }>("/sync/concepts", { method: "POST" }),
  syncIndustry: () => http<{ task_id: number }>("/sync/industry", { method: "POST" }),
  syncAnalyst: () => http<{ task_type: string; status: string }>("/sync/analyst", { method: "POST" }),
  syncCandles: (days = 365) =>
    http<{ task_type: string; status: string; days: number }>(
      `/sync/candles?days=${days}`,
      { method: "POST" },
    ),
  syncTasks: () => http<SyncTask[]>("/sync/tasks"),
  dbStats: () => http<DbStats>("/sync/db-stats"),
  // Backtest
  backtest: (params: {
    code: string; strategy: string; period: string;
    stop_loss: number; target: number;
    volume_filter: boolean; shrink_filter: boolean;
    close_above_support: boolean; weekly_confluence: boolean;
  }) =>
    http<import("../types").BacktestResponse>("/backtest", {
      method: "POST",
      body: JSON.stringify(params),
    }),
};
