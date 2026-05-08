import type {
  DbStats,
  MarketOverview,
  ScreenerResponse,
  SourceConfig,
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
  financialHistory: (code: string) =>
    http<{ code: string; history: { period: string; eps: number; roe: number; revenue: number; net_profit: number; revenue_yoy: number; net_profit_yoy: number }[] }>(
      `/stocks/${code}/financial-history`,
    ),
  srFactors: () =>
    http<import("../types").SrFactor[]>("/stocks/meta/sr-factors"),
  screener: (pattern: string, limit = 50) =>
    http<ScreenerResponse>(`/screener/${pattern}?limit=${limit}`),
  triggerScan: () =>
    http<{ status: string; message: string }>("/screener/scan", { method: "POST" }),
  screenerHistory: (pattern: string) =>
    http<{ ts: string; scanned_at: string; total: number; scanned: number }[]>(`/screener/history/${pattern}`),
  screenerSnapshot: (pattern: string, ts: string, limit = 200) =>
    http<ScreenerResponse>(`/screener/history/${pattern}/${ts}?limit=${limit}`),
  screenerConfig: () =>
    http<Record<string, Record<string, unknown>>>("/screener/config"),
  updateScreenerConfig: (pattern: string, params: Record<string, unknown>) =>
    http<{ status: string }>("/screener/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern, params }),
    }),
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
  syncFinancialHistory: (years = 5) =>
    http<{ task_type: string; status: string }>(`/sync/financial_history?years=${years}`, { method: "POST" }),
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
  // Data sources
  getSources: () => http<SourceConfig>("/sync/sources"),
  putSources: (sources: Record<string, string>) =>
    http<{ ok: boolean; error?: string }>("/sync/sources", {
      method: "PUT",
      body: JSON.stringify({ sources }),
    }),
  // Schedule config
  getSchedule: () => http<Record<string, { enabled: boolean; cron: string; label: string; desc: string; next_run?: string }>>("/sync/schedule"),
  putSchedule: (schedules: Record<string, { enabled: boolean; cron: string }>) =>
    http<{ ok: boolean; error?: string }>("/sync/schedule", {
      method: "PUT",
      body: JSON.stringify({ schedules }),
    }),
  // Backtest
  backtest: (params: Record<string, string | number | boolean>) =>
    http<import("../types").BacktestResponse>("/backtest", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  // Algo modules
  algoStatus: () =>
    http<{ modules: import("../types").AlgoModule[] }>("/algo/status"),
  optimize: (params: Record<string, string | number>) =>
    http<import("../types").OptimizeResult>("/algo/optimize", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  mlTrain: (params: Record<string, unknown>) =>
    http<import("../types").MlTrainResult>("/algo/ml/train", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  mlScore: (code: string) =>
    http<{ code: string; ml_score: number; error?: string }>(`/algo/ml/score/${code}`),
  rlTrain: (params: Record<string, unknown>) =>
    http<Record<string, unknown>>("/algo/rl/train", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  rlPosition: (code: string) =>
    http<{ code: string; allocation: number; action: number; error?: string }>(`/algo/rl/position/${code}`),
  regimeFit: (params: Record<string, unknown>) =>
    http<import("../types").RegimeFitResult>("/algo/regime/fit", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  regimePredict: (code: string) =>
    http<Record<string, unknown>>(`/algo/regime/${code}`),
  patternDtw: (code: string) =>
    http<import("../types").PatternResult>(`/algo/pattern/dtw/${code}`),
  patternCnnTrain: (params: Record<string, unknown>) =>
    http<Record<string, unknown>>("/algo/pattern/cnn/train", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  patternCnn: (code: string) =>
    http<import("../types").PatternResult>(`/algo/pattern/cnn/${code}`),
  // Signal
  signal: (params: Record<string, unknown>) =>
    http<import("../types").TradeSignal>("/backtest/signal", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  // AI Strategy
  aiStatus: () =>
    http<import("../types").AiModelStatus>("/strategy/status"),
  aiIndustryStocks: (code: string, limit = 20) =>
    http<{ code: string; industry: string; stocks: { code: string; name: string }[] }>(
      `/strategy/industry_stocks/${code}?limit=${limit}`,
    ),
  aiLabels: (code: string, params?: Record<string, unknown>) =>
    http<{ code: string; method: string; points: import("../types").LabeledPoint[]; candles: import("../types").Candle[] }>(
      `/strategy/labels/${code}`,
      { method: "POST", body: JSON.stringify(params || {}) },
    ),
  aiTrain: (params: Record<string, unknown>) =>
    http<import("../types").AiTrainResult>("/strategy/train", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  aiPredict: (code: string, modelType = "lightgbm") =>
    http<import("../types").AiPrediction>(`/strategy/predict/${code}?model_type=${modelType}`),
  aiSignal: (code: string, params?: Record<string, unknown>) =>
    http<import("../types").AiSignal>(`/strategy/signal/${code}`, {
      method: "POST",
      body: JSON.stringify(params || {}),
    }),
  aiBacktest: (params: Record<string, unknown>) =>
    http<import("../types").AiBacktestResult>("/strategy/backtest", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  aiTrainMarket: (params: Record<string, unknown>) =>
    http<{ task_id: string; status: string; message: string }>("/strategy/train_market", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  aiTrainProgress: () =>
    http<Array<{
      task_id: string; model_type: string; max_stocks: number; epochs: number;
      status: string; progress: number; message: string;
      started_at: number; ended_at: number | null;
      result: Record<string, unknown> | null;
      codes_used: number; total_codes: number;
    }>>("/strategy/train_progress"),
  // AI Scanner
  aiScan: (params: Record<string, unknown>) =>
    http<{ task_id: string; status: string; message: string }>("/strategy/scan", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  aiScanProgress: () =>
    http<Array<{
      task_id: string; scope: string; status: string; progress: number;
      message: string; total: number; scanned: number;
      started_at: number; ended_at: number | null;
      results: Array<{
        code: string; name: string; model: string; action: string;
        confidence: number; current_price: number; entry_price: number;
        stop_loss: number; target_price: number; risk_reward: number;
        trend: string; reason: string;
      }>;
    }>>("/strategy/scan_progress"),
  aiScanCancel: (taskId: string) =>
    http<{ status: string }>(`/strategy/scan_progress/${taskId}`, { method: "DELETE" }),
  aiScanClear: () =>
    http<{ removed: number }>("/strategy/scan_progress", { method: "DELETE" }),
};
