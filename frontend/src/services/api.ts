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
    min_score?: number;
  }) => {
    const q = new URLSearchParams();
    if (opts?.period) q.set("period", opts.period);
    if (opts?.lookback) q.set("lookback", String(opts.lookback));
    if (opts?.sensitivity) q.set("sensitivity", String(opts.sensitivity));
    if (opts?.algorithm) q.set("algorithm", opts.algorithm);
    if (opts?.factor_weights) q.set("factor_weights", JSON.stringify(opts.factor_weights));
    if (opts?.min_score !== undefined) q.set("min_score", String(opts.min_score));
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
  screenerByCodes: (codes: string[]) =>
    http<Record<string, { pattern: string; label: string; score: number; scanned_at: string }[]>>(
      `/screener/by_codes?codes=${encodeURIComponent(codes.join(","))}`,
    ),
  triggerScan: (pattern?: string) =>
    http<{ status: string; message: string }>(
      `/screener/scan${pattern ? `?pattern=${encodeURIComponent(pattern)}` : ""}`,
      { method: "POST" },
    ),
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
      results: AiScanHit[];
    }>>("/strategy/scan_progress"),
  aiScanCancel: (taskId: string) =>
    http<{ status: string }>(`/strategy/scan_progress/${taskId}`, { method: "DELETE" }),
  aiScanClear: () =>
    http<{ removed: number }>("/strategy/scan_progress", { method: "DELETE" }),
  aiScanHistory: (limit = 50) =>
    http<Array<{
      ts: string; scope: string; scope_code: string; model_types: string[];
      scanned: number; total: number; hits_total: number;
      started_at: number | null; ended_at: number | null;
    }>>(`/strategy/scan_history?limit=${limit}`),
  aiScanSnapshot: (ts: string) =>
    http<{
      ts: string; scope: string; model_types: string[];
      scanned: number; total: number; hits_total: number;
      started_at: number; ended_at: number;
      results: AiScanHit[];
      error?: string;
    }>(`/strategy/scan_history/${ts}`),

  // ── Recommender (v2 strategy) ──
  recommendStyles: () =>
    http<{ key: string; label: string }[]>("/recommend/styles"),
  recommendScan: (opts?: { styles?: string[]; top_n?: number; min_score?: number }) => {
    const q = new URLSearchParams();
    if (opts?.styles?.length) q.set("styles", opts.styles.join(","));
    if (opts?.top_n) q.set("top_n", String(opts.top_n));
    if (opts?.min_score !== undefined) q.set("min_score", String(opts.min_score));
    const qs = q.toString();
    return http<{ scan_id: string; status: string; styles?: string[] }>(
      `/recommend/scan${qs ? "?" + qs : ""}`, { method: "POST" },
    );
  },
  recommendScanProgress: (scanId: string) =>
    http<import("../types").RecommendScanProgress>(`/recommend/scan/${scanId}`),
  recommendCurrentScan: () =>
    http<{ running: boolean } & Partial<import("../types").RecommendScanProgress>>(
      "/recommend/scan",
    ),
  recommendList: (opts?: { style?: string; scan_date?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (opts?.style) q.set("style", opts.style);
    if (opts?.scan_date) q.set("scan_date", opts.scan_date);
    if (opts?.limit) q.set("limit", String(opts.limit));
    const qs = q.toString();
    return http<import("../types").RecommendListResp>(
      `/recommend/list${qs ? "?" + qs : ""}`,
    );
  },
  recommendDetail: (code: string, rebuild = false) =>
    http<{ code: string; from: "db" | "live"; items: import("../types").Recommendation[] }>(
      `/recommend/stock/${code}${rebuild ? "?rebuild=true" : ""}`,
    ),

  // ── Recommender ML training ──
  recommendTrain: (
    model: import("../types").MlModelKey,
    params?: {
      horizon_days?: number;
      universe_limit?: number;
      history_years?: number;
      epochs?: number;
      total_timesteps?: number;
    },
  ) => {
    const q = new URLSearchParams();
    if (params) for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) q.set(k, String(v));
    }
    const qs = q.toString();
    return http<{ job_id: string; status: string; model: string; params: Record<string, unknown> }>(
      `/recommend/train/${model}${qs ? "?" + qs : ""}`, { method: "POST" },
    );
  },
  recommendTrainProgress: (jobId: string) =>
    http<import("../types").MlTrainProgress>(`/recommend/train/${jobId}`),
  recommendTrainStatus: () =>
    http<import("../types").MlTrainStatusResp>("/recommend/trainings"),
  recommendMarketEnv: () =>
    http<{
      code: string; trend: number; atr_pct: number; verdict: string;
      recent_closes: [string, number][];
    }>("/recommend/index/env"),
  recommendSyncIndices: () =>
    http<Record<string, number>>("/recommend/sync_indices", { method: "POST" }),
  recommendLifecycleStats: (style?: string, days = 90) =>
    http<{
      n: number; completed?: number; by_state: Record<string, number>;
      win_rate?: number; avg_return?: number; best?: number; worst?: number;
    }>(`/recommend/lifecycle/stats?days=${days}${style ? `&style=${style}` : ""}`),
  recommendLifecycleUpdate: (lookbackDays = 60) =>
    http<{ n_processed: number; states: Record<string, number> }>(
      `/recommend/lifecycle/update?lookback_days=${lookbackDays}`,
      { method: "POST" },
    ),
  recommendLifecycleRecent: (style?: string, state?: string, limit = 50) => {
    const qs = new URLSearchParams();
    if (style) qs.set("style", style);
    if (state) qs.set("state", state);
    qs.set("limit", String(limit));
    return http<Array<{
      id: number; code: string; name: string; style: string; scan_date: string;
      state: string; exit_reason: string;
      buy_low: number; buy_high: number; stop_loss: number;
      take_profit_1: number; take_profit_2: number; initial_price: number;
      triggered_date: string; triggered_price: number;
      exit_date: string; exit_price: number;
      max_favorable_pct: number; max_adverse_pct: number;
      realized_return_pct: number;
      days_to_trigger: number; days_held: number;
    }>>(`/recommend/lifecycle/recent?${qs.toString()}`);
  },

  // ── Dragon Strategy (龙头战法) ──
  dragonStatus: () => http<import("../types").DragonStatus>("/dragon/status"),
  dragonMarketCycle: (date?: string) =>
    http<import("../types").MarketCycle>(
      `/dragon/market-cycle${date ? `?date=${date}` : ""}`,
    ),
  dragonZtPool: (params?: { date?: string; pool_type?: "zt" | "zb" | "dt"; min_consecutive?: number; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.date) q.set("date", params.date);
    if (params?.pool_type) q.set("pool_type", params.pool_type);
    if (params?.min_consecutive !== undefined) q.set("min_consecutive", String(params.min_consecutive));
    if (params?.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return http<{ trade_date: string; pool_type: string; count: number; items: import("../types").ZtPoolItem[] }>(
      `/dragon/zt-pool${qs ? "?" + qs : ""}`,
    );
  },
  dragonLhb: (code: string, limit = 30) =>
    http<{
      code: string;
      records: import("../types").LhbRecord[];
      seats: Record<string, import("../types").LhbSeat[]>;
    }>(`/dragon/lhb/${code}?limit=${limit}`),
  dragonBoardHeat: (params?: { date?: string; limit?: number; history_days?: number }) => {
    const q = new URLSearchParams();
    if (params?.date) q.set("date", params.date);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.history_days) q.set("history_days", String(params.history_days));
    const qs = q.toString();
    return http<{ trade_date: string; items: import("../types").BoardHeatItem[] }>(
      `/dragon/board-heat${qs ? "?" + qs : ""}`,
    );
  },
  dragonKnowledge: () =>
    http<{ buy: Record<string, string>; sell: Record<string, string>; cycle: Record<string, string> }>(
      "/dragon/knowledge",
    ),
  dragonTrain: (params: { start_date: string; end_date: string; epochs?: number; train_stage2?: boolean }) =>
    http<{ task_id: string; status: string }>("/dragon/train", {
      method: "POST", body: JSON.stringify(params),
    }),
  dragonTrainProgress: () =>
    http<import("../types").DragonTrainJob[]>("/dragon/train_progress"),
  dragonTrainClear: (tid?: string) =>
    http<{ status?: string; removed?: number }>(
      `/dragon/train_progress${tid ? `/${tid}` : ""}`,
      { method: "DELETE" },
    ),
  dragonScan: (params?: { date?: string; threshold?: number; top_n?: number; persist?: boolean }) =>
    http<{ task_id: string; status: string }>("/dragon/scan", {
      method: "POST", body: JSON.stringify(params || {}),
    }),
  dragonScanProgress: () =>
    http<import("../types").DragonScanJob[]>("/dragon/scan_progress"),
  dragonScanClear: (tid?: string) =>
    http<{ status?: string; removed?: number }>(
      `/dragon/scan_progress${tid ? `/${tid}` : ""}`,
      { method: "DELETE" },
    ),
  dragonTodaySignals: (date?: string, limit = 50) =>
    http<{ trade_date: string; items: import("../types").DragonSignalRow[] }>(
      `/dragon/scan/today?limit=${limit}${date ? `&date=${date}` : ""}`,
    ),
  dragonSignal: (code: string, date?: string) =>
    http<import("../types").DragonSignalDetail>(
      `/dragon/signal/${code}${date ? `?date=${date}` : ""}`,
    ),
  dragonBacktest: (params: Record<string, unknown>) =>
    http<import("../types").DragonBacktestResult>("/dragon/backtest", {
      method: "POST", body: JSON.stringify(params),
    }),
  dragonSync: (task: "zt_pool" | "lhb" | "concept_heat_history" | "dragon_all", date_str?: string) =>
    http<{ task: string; status: string }>("/dragon/sync", {
      method: "POST", body: JSON.stringify({ task, date_str }),
    }),
  dragonBackfill: (params: {
    start_date?: string; end_date?: string; days?: number;
    include_zt?: boolean; include_lhb?: boolean; include_concept?: boolean;
    sleep_sec?: number;
  }) =>
    http<{ task: string; status: string }>("/dragon/backfill", {
      method: "POST", body: JSON.stringify(params),
    }),

  // ── LLM精选 ──
  llmProviders: () =>
    http<{ providers: LlmProvider[] }>("/llmpick/providers"),
  llmValidate: (params: {
    candidates: { code: string; name?: string; logic?: string; risk?: string; theme?: string }[];
    pe_max_pctile?: number;
    crowding_max_pctile?: number;
    require_above_ma20?: boolean;
    require_positive_flow?: boolean;
  }) =>
    http<LlmPickResult>("/llmpick/validate", {
      method: "POST", body: JSON.stringify(params),
    }),
  llmGenerate: (params: {
    provider?: string;
    prompt?: string;
    auto_validate?: boolean;
    pe_max_pctile?: number;
    crowding_max_pctile?: number;
    require_above_ma20?: boolean;
    require_positive_flow?: boolean;
  }) =>
    http<LlmPickResult & { llm?: { provider: string; model: string; raw_response: string; candidate_count: number }; message?: string }>(
      "/llmpick/generate", { method: "POST", body: JSON.stringify(params) },
    ),
  llmStatus: () =>
    http<{ task: { status: string; provider?: string; message?: string; error?: string } | null }>("/llmpick/status"),
  llmHistory: (limit = 20) =>
    http<{ history: { ts: string; mode: string; total: number; passed: number; provider: string }[] }>(
      `/llmpick/history?limit=${limit}`,
    ),
  llmHistoryDetail: (ts: string) =>
    http<LlmPickResult>(`/llmpick/history/${ts}`),
  llmDefaultPrompt: () =>
    http<{ prompt: string }>("/llmpick/default_prompt"),
};

export type LlmProvider = {
  key: string;
  label: string;
  configured: boolean;
};

export type LlmPickItem = {
  code: string;
  name: string;
  logic: string;
  risk: string;
  theme: string;
  close: number;
  change_pct: number | null;
  amount: number | null;
  turnover_rate: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  pe_percentile: number | null;
  roe: number | null;
  revenue_yoy: number | null;
  net_profit_yoy: number | null;
  amount_ratio: number | null;
  amount_pctile: number | null;
  vol_ma5_ratio: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma_aligned: boolean;
  above_ma20: boolean;
  pass_valuation: boolean;
  pass_flow: boolean;
  pass_crowding: boolean;
  pass_technical: boolean;
  passed: boolean;
  total_score: number;
  fail_reasons: string[];
  industry: string;
  market: string;
  concepts: string[];
  sparkline: number[];
};

export type LlmPickResult = {
  total: number;
  passed: number;
  filtered: number;
  results: LlmPickItem[];
};

export type AiScanHit = {
  code: string;
  name: string;
  action: "buy" | "sell";
  confidence: number;
  agreement: number;
  models_total: number;
  models_agree: number;
  rating: number;
  current_price: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  risk_reward: number;
  trend: string;
  sparkline: number[];
  industry: string;
  market: string;
  concepts: string[];
  pe: number | null;
  roe: number | null;
  market_cap: number | null;
  change_pct: number | null;
  amount: number | null;
  turnover_rate: number | null;
  fundamental_status: string;
  triggers: string[];
  model_hits: { model: string; action: string; confidence: number; rr: number }[];
  // Legacy
  model: string;
  reason: string;
};
