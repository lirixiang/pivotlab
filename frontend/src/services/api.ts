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
  scanWatchlistPatterns: () =>
    http<{
      scanned: number; total_hits: number;
      counts: Record<string, number>; labels: Record<string, string>;
      scanned_at: string;
    }>("/watchlist/scan-patterns", { method: "POST" }),
  // OCR: extract stock codes from a screenshot (multipart)
  ocrExtractCodes: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(BASE + "/ocr/extract-codes", { method: "POST", body: fd }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      return r.json() as Promise<{
        candidates: {
          code: string; name: string; industry: string;
          valid: boolean; in_watchlist: boolean; confidence: number; text: string;
        }[];
      }>;
    });
  },
  importWatchlist: (codes: string[]) =>
    http<{ added: number; skipped_existing: number; skipped_unknown: number; added_codes: string[] }>(
      "/ocr/import-watchlist",
      { method: "POST", body: JSON.stringify({ codes }) },
    ),
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
    http<LlmPickResult & { llm?: { provider: string; model: string; raw_response: string; candidate_count: number }; mode?: string }>(`/llmpick/history/${ts}`),
  llmDefaultPrompt: () =>
    http<{ prompt: string }>("/llmpick/default_prompt"),

  // ── Quant Trading Systems (M1) ─────────────────────────────
  quantDefaults: () => http<QuantSystemConfig>("/quant/defaults"),
  quantList: () => http<QuantSystemSummary[]>("/quant/systems"),
  quantGet: (id: number) => http<QuantSystem>(`/quant/systems/${id}`),
  quantCreate: (body: Partial<QuantSystemConfig> & { name?: string }) =>
    http<QuantSystem>("/quant/systems", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  quantUpdate: (id: number, body: Partial<QuantSystemConfig> & { name?: string; description?: string | null; status?: string }) =>
    http<QuantSystem>(`/quant/systems/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  quantDelete: (id: number) =>
    http<{ ok: boolean; id: number }>(`/quant/systems/${id}`, { method: "DELETE" }),
  quantTest: (id: number, body: { code: string; date?: string; lookback?: number }) =>
    http<QuantTestResult>(`/quant/systems/${id}/test`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // ── M3 ──
  quantRun: (id: number, body?: { end_date?: string }) =>
    http<QuantRunResult>(`/quant/systems/${id}/run`, {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
  quantRuns: (id: number, limit = 20) =>
    http<QuantRunSummary[]>(`/quant/systems/${id}/runs?limit=${limit}`),
  quantRunDetail: (runId: number) =>
    http<QuantRunResult & { id: number; system_id: number; created_at: string }>(
      `/quant/runs/${runId}`,
    ),
  quantRunDelete: (runId: number) =>
    http<{ ok: boolean; id: number }>(`/quant/runs/${runId}`, { method: "DELETE" }),
  // ── M4 回测 ──
  quantBacktest: (
    id: number,
    body: {
      start_date: string;
      end_date: string;
      name?: string;
      commission_bps?: number;
      slippage_bps?: number;
      initial_capital?: number;
    },
  ) =>
    http<QuantBacktestResult>(`/quant/systems/${id}/backtest`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  quantBacktestStock: (
    id: number,
    body: {
      code: string;
      start_date: string;
      end_date: string;
      commission_bps?: number;
      slippage_bps?: number;
    },
  ) =>
    http<QuantBacktestResult & { system_id: number; code: string }>(
      `/quant/systems/${id}/backtest-stock`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  quantBacktestsList: (id: number, limit = 30) =>
    http<QuantBacktestSummary[]>(`/quant/systems/${id}/backtests?limit=${limit}`),
  quantBacktestDetail: (bid: number) =>
    http<QuantBacktestDetail>(`/quant/backtests/${bid}`),
  quantBacktestDelete: (bid: number) =>
    http<{ ok: boolean; id: number }>(`/quant/backtests/${bid}`, { method: "DELETE" }),

  // ── M5: Journal / 实盘账本 ──
  quantPositions: (systemId: number, status: "open" | "closed" | "all" = "open") =>
    http<QuantPositionRow[]>(`/quant/systems/${systemId}/positions?status=${status}`),
  quantPositionFromOrder: (body: {
    run_id: number;
    order_index: number;
    actual_price?: number;
    actual_qty?: number;
    commission?: number;
    notes?: string;
  }) =>
    http<QuantPositionRow>(`/quant/positions/from-order`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  quantPositionManual: (body: {
    system_id: number;
    code: string;
    name?: string;
    qty: number;
    entry_price: number;
    entry_date: string;
    stop_price?: number;
    commission?: number;
    notes?: string;
  }) =>
    http<QuantPositionRow>(`/quant/positions/manual`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  quantPositionClose: (
    pid: number,
    body: { exit_price: number; exit_date: string; exit_reason?: string; commission?: number },
  ) =>
    http<QuantPositionRow>(`/quant/positions/${pid}/close`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  quantPositionEdit: (pid: number, body: { stop_price?: number; notes?: string }) =>
    http<QuantPositionRow>(`/quant/positions/${pid}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  quantPositionDelete: (pid: number) =>
    http<{ ok: boolean; id: number }>(`/quant/positions/${pid}`, { method: "DELETE" }),
  quantTrades: (systemId: number, limit = 200) =>
    http<QuantPositionRow[]>(`/quant/systems/${systemId}/trades?limit=${limit}`),
  quantNavSnapshot: (systemId: number, body?: { trade_date?: string }) =>
    http<QuantNavRow & { snapshot: QuantNavSnapshotRow[] }>(
      `/quant/systems/${systemId}/nav/snapshot`,
      { method: "POST", body: JSON.stringify(body || {}) },
    ),
  quantNav: (systemId: number, opts?: { from_date?: string; to_date?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (opts?.from_date) p.set("from_date", opts.from_date);
    if (opts?.to_date) p.set("to_date", opts.to_date);
    if (opts?.limit) p.set("limit", String(opts.limit));
    const qs = p.toString();
    return http<QuantNavRow[]>(`/quant/systems/${systemId}/nav${qs ? "?" + qs : ""}`);
  },
  quantJournalSummary: (systemId: number) =>
    http<QuantJournalSummary>(`/quant/systems/${systemId}/journal/summary`),

  // ── Sector Pool (赛道池) ────────────────────────────────
  sectorPoolList: (includeArchived = false) =>
    http<{ items: SectorPool[] }>(`/sector-pool${includeArchived ? "?include_archived=true" : ""}`),
  sectorPoolCreate: (body: { name: string; category?: string; description?: string; rank?: number }) =>
    http<SectorPool>("/sector-pool", { method: "POST", body: JSON.stringify(body) }),
  sectorPoolUpdate: (id: number, body: Partial<{ name: string; category: string; description: string; rank: number; status: "active" | "archived" }>) =>
    http<SectorPool>(`/sector-pool/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  sectorPoolDelete: (id: number) =>
    http<{ ok: boolean; hard_deleted: boolean }>(`/sector-pool/${id}`, { method: "DELETE" }),
  sectorPoolStocks: (sectorId: number) =>
    http<{ items: SectorPoolStock[] }>(`/sector-pool/${sectorId}/stocks`),
  sectorPoolAddStock: (sectorId: number, body: { code: string; tier?: number; note?: string }) =>
    http<SectorPoolStock>(`/sector-pool/${sectorId}/stocks`, { method: "POST", body: JSON.stringify(body) }),
  sectorPoolBulkAdd: (sectorId: number, codes: string[], tier = 2) =>
    http<{ added: number; skipped_existing: number; skipped_unknown: number; added_codes: string[] }>(
      `/sector-pool/${sectorId}/stocks/bulk`,
      { method: "POST", body: JSON.stringify({ codes, tier }) },
    ),
  sectorPoolUpdateStock: (sectorId: number, code: string, body: { tier?: number; note?: string }) =>
    http<SectorPoolStock>(`/sector-pool/${sectorId}/stocks/${code}`, { method: "PATCH", body: JSON.stringify(body) }),
  sectorPoolRemoveStock: (sectorId: number, code: string) =>
    http<{ ok: boolean }>(`/sector-pool/${sectorId}/stocks/${code}`, { method: "DELETE" }),
  sectorPoolCodesUnion: (poolIds: number[], tierMax = 3) =>
    http<{ codes: string[]; total: number; by_pool: Record<string, string[]> }>(
      `/sector-pool/codes/union?pool_ids=${poolIds.join(",")}&tier_max=${tierMax}`,
    ),
};

// ── Quant types ──────────────────────────────────────────────
export type QuantRuleExpr = { expr: string; desc?: string };

export type QuantUniverseCfg = {
  base: string;
  filters: QuantRuleExpr[];
  exclude_codes: string[];
  max_size: number;
  sector_pool_ids?: number[];
  sector_pool_tier_max?: number;
};

// ── Sector Pool types ────────────────────────────────────────
export type SectorPool = {
  id: number;
  name: string;
  category: string;
  description: string;
  rank: number;
  status: "active" | "archived";
  stock_count: number;
  created_at: string | null;
  updated_at: string | null;
};

export type SectorPoolStock = {
  id: number;
  sector_id: number;
  code: string;
  name: string;
  industry: string;
  tier: number;
  note: string;
  added_at: string | null;
};

export type QuantOptionalBlock = {
  min_match: number;
  rules: QuantRuleExpr[];
};

export type QuantSignalSideCfg = {
  all_of?: QuantRuleExpr[];
  any_of?: QuantRuleExpr[];
  optional?: QuantOptionalBlock;
};

export type QuantSignalCfg = {
  buy: QuantSignalSideCfg;
  sell: QuantSignalSideCfg;
};

export type QuantStopLoss = {
  type: "ma" | "percent" | "atr";
  ma_period?: number;
  percent?: number;
  atr_period?: number;
  atr_mult?: number;
};

export type QuantRiskCfg = {
  per_stock_max_pct: number;
  total_position_max_pct: number;
  per_trade_max_loss_pct: number;
  stop_loss: QuantStopLoss;
  trailing_stop: boolean;
  drawdown_breaker_pct: number;
};

export type QuantExecCfg = {
  mode: "semi_auto" | "manual";
  order_type: string;
  max_orders_per_day: number;
  notify: { channel: string; enabled: boolean };
};

export type QuantSystemConfig = {
  name: string;
  description: string;
  status: "draft" | "active" | "paused";
  initial_capital: number;
  universe_cfg: QuantUniverseCfg;
  signal_cfg: QuantSignalCfg;
  risk_cfg: QuantRiskCfg;
  exec_cfg: QuantExecCfg;
};

export type QuantSystem = QuantSystemConfig & {
  id: number;
  created_at: string;
  updated_at: string;
};

export type QuantSystemSummary = {
  id: number;
  name: string;
  description: string;
  status: "draft" | "active" | "paused";
  initial_capital: number;
  created_at: string;
  updated_at: string;
};

// 试运行结果（M2）
export type QuantRuleEvalResult = {
  expr: string;
  desc: string;
  passed: boolean;
  value: number | null;
  error: string | null;
};

export type QuantSideReport = {
  triggered: boolean;
  combine: "all_of" | "any_of" | "all_of+optional" | "empty";
  rules: QuantRuleEvalResult[];
  core_count?: number;
  min_match?: number;
};

export type QuantTestResult = {
  code: string;
  date: string | null;
  buy: QuantSideReport;
  sell: QuantSideReport;
  snapshot: {
    open: number;
    high: number;
    low: number;
    close: number;
    vol: number;
    bars: number;
  };
};

// ── M3: 完整 Pipeline run ──
export type QuantCandidate = {
  code: string;
  name: string;
  industry: string;
  last_date: string;
  last_close: number;
  last_amount: number;
};

export type QuantSignalRecord = {
  code: string;
  name: string;
  side: "buy" | "sell";
  price: number;
  date: string;
  rules_hit: { expr: string; desc: string; value: number | null }[];
  // M5: 持仓卖出信号（持仓 code 触发的 sell）携带账本信息
  qty?: number;
  entry_price?: number;
  stop_price?: number;
  pnl_pct?: number;
  position_id?: number;
  reasons?: string[];
};

export type QuantOrder = {
  code: string;
  name: string;
  action: string;
  price: number;
  qty: number;
  stop_price: number;
  est_loss: number;
  notional: number;
  risk_used_pct: number;
  reason: string;
  rejected: boolean;
  reject_reason: string;
};

export type QuantRunMetrics = {
  universe_total_scanned?: number;
  universe_passed?: number;
  buy_signals?: number;
  sell_signals?: number;
  orders_rejected?: number;
  capital_used?: number;
  capital_used_pct?: number;
};

export type QuantRunResult = {
  run_id?: number;
  trade_date: string;
  candidates: QuantCandidate[];
  signals: QuantSignalRecord[];
  orders: QuantOrder[];
  universe_count: number;
  signal_count: number;
  order_count: number;
  duration_ms: number;
  error: string;
  metrics: QuantRunMetrics;
};

export type QuantRunSummary = {
  id: number;
  run_type: string;
  trade_date: string;
  universe_count: number;
  signal_count: number;
  order_count: number;
  duration_ms: number;
  metrics: QuantRunMetrics;
  created_at: string;
  error: string;
};

// ── M4: 回测 ──
export type QuantEquityPoint = {
  date: string;
  equity: number;
  cash: number;
  positions_value: number;
  n_positions: number;
  drawdown_pct: number;
};

export type QuantTrade = {
  code: string;
  name: string;
  side: "open" | "close";
  qty: number;
  price: number;
  date: string;
  // open
  stop_price?: number;
  est_loss?: number;
  notional?: number;
  // close
  pnl?: number;
  pnl_pct?: number;
  hold_days?: number;
  reason: string;
};

export type QuantPositionEnd = {
  code: string;
  name: string;
  qty: number;
  entry_price: number;
  entry_date: string;
  stop_price: number;
  last_price: number;
  market_value: number;
  cost_basis: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
};

export type QuantBacktestMetrics = {
  total_return_pct: number;
  cagr_pct: number;
  max_drawdown_pct: number;
  sharpe: number;
  win_rate_pct: number;
  trade_count: number;
  win_count: number;
  loss_count: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  profit_factor: number;
  exposure_pct: number;
  final_equity: number;
};

export type QuantBacktestResult = {
  backtest_id?: number;
  start_date: string;
  end_date: string;
  initial_capital: number;
  trading_days: number;
  equity_curve: QuantEquityPoint[];
  trades: QuantTrade[];
  positions_end: QuantPositionEnd[];
  metrics: QuantBacktestMetrics;
  duration_ms: number;
  error: string;
};

export type QuantBacktestSummary = {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  status: string;
  trading_days: number;
  metrics: QuantBacktestMetrics;
  duration_ms: number;
  error: string;
  created_at: string;
};

export type QuantBacktestDetail = QuantBacktestResult & {
  id: number;
  system_id: number;
  name: string;
  status: string;
  system_snapshot: Record<string, any>;
  params: Record<string, any>;
  created_at: string;
};

// ── M5: Journal ──
export type QuantPositionRow = {
  id: number;
  system_id: number;
  code: string;
  name: string;
  qty: number;
  entry_price: number;
  entry_date: string;
  stop_price: number;
  cost_basis: number;
  commission_in: number;
  status: "open" | "closed";
  exit_price: number;
  exit_date: string;
  exit_reason: string;
  commission_out: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
  source_run_id: number;
  source_order_index: number;
  notes: string;
  created_at: string;
  updated_at: string;
};

export type QuantNavRow = {
  trade_date: string;
  cash: number;
  positions_value: number;
  equity: number;
  n_positions: number;
  realized_pnl_total: number;
  unrealized_pnl: number;
  drawdown_pct: number;
};

export type QuantNavSnapshotRow = {
  code: string;
  name: string;
  qty: number;
  entry_price: number;
  last_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
};

export type QuantJournalSummary = {
  initial_capital: number;
  open_count: number;
  open_cost: number;
  closed_count: number;
  realized_pnl_total: number;
  realized_pnl_pct: number;
  win_count: number;
  loss_count: number;
  win_rate_pct: number;
  profit_factor: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  avg_hold_days: number;
  latest_nav: {
    trade_date: string;
    cash: number;
    positions_value: number;
    equity: number;
    drawdown_pct: number;
    n_positions: number;
  } | null;
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
