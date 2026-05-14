export type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  /** 盘中尚未收盘 / 量能按已交易时间外推得到的预估柱,前端用虚框区分 */
  estimated?: boolean;
};

export type Level = {
  label: string;
  price: number;
  kind: "resistance" | "support";
  strength: number;
  touches: number;
  note?: string;
  distance_pct: number;
  score?: number;          // 0-100 confidence (normalised)
  factors?: Record<string, number | boolean>;  // score breakdown details
  reasons?: string[];      // human-readable reason tags
};

export type SrFactor = {
  key: string;
  label: string;
  default_weight: number;
};

export type StockQuote = {
  code: string;
  name: string;
  price: number;
  change: number;
  change_pct: number;
  volume: number;
  amount: number;
  volume_ratio: number;
  turnover: number;
  industry: string;
  market: string;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  turnover_rate: number;
  pe_ratio: number;
  market_cap: number;
  industry_pe: { industry: string; avg_pe: number; stock_count: number } | null;
  concepts: string[];
  concept_details: ConceptDetail[];
  fundamentals: FundamentalSnapshot | null;
  analyst_consensus: AnalystConsensus | null;
};

export type ConceptDetail = {
  concept: string;
  board_code: string | null;
  rank: number | null;
  change_pct_1d: number | null;
  heat_level: 'core' | 'hot' | 'watch' | 'observe';
  heat_label: string;
  heat_tone: 'concept-hot' | 'concept-watch' | 'concept-neutral';
  is_hot_theme: boolean;
};

export type FundamentalSnapshot = {
  report_period: string;
  eps_ttm: number;
  roe: number;
  revenue_yoy: number;
  net_profit_yoy: number;
  pe_ratio_ttm: number;
  total_revenue: number;
  net_profit: number;
  fundamental_status: "healthy" | "neutral" | "weak" | "risk" | "unknown";
  fundamental_summary: string;
};

export type AnalystConsensus = {
  consensus_target: number | null;
  target_high: number | null;
  target_low: number | null;
  analyst_count: number;
  buy_count: number;
  overweight_count: number;
  neutral_count: number;
  underweight_count: number;
  sell_count: number;
  eps_current_year: number | null;
  eps_next_year: number | null;
};

export type SyncTask = {
  id: number;
  task_type: string;
  status: string;
  total: number;
  processed: number;
  error_msg: string;
  started_at: string | null;
  finished_at: string | null;
};

export type DbStats = {
  stocks: number;
  daily_candles: number;
  candle_codes: number;
  candle_min_date: string;
  candle_max_date: string;
  quote_cache: number;
  financial_snapshots: number;
  stock_concepts: number;
  analyst_consensus: number;
  sync_tasks: number;
};

export type DataSource = {
  id: string;
  name: string;
  desc: string;
  url: string;
  status: "ok" | "blocked" | "deprecated";
  default?: boolean;
  selected: boolean;
};

export type SourceConfig = Record<string, {
  selected: string;
  sources: DataSource[];
}>;

export type StockDetail = {
  quote: StockQuote;
  candles: Candle[];
  levels: Level[];
};

export type ScreenerItem = {
  code: string;
  name: string;
  pattern: string;
  score: number;
  price: number;
  change_pct: number;
  volume_ratio: number;
  breakout_price?: number | null;
  pullback_price?: number | null;
  distance_to_support_pct?: number | null;
  triggers: string[];
  market?: string;
  industry?: string;
  market_cap?: number;
  amount?: number;
  rr_ratio?: number;
  support_score?: number;
  concept?: string;
  fundamental_status?: string;
  fundamental_summary?: string;
};

export type ScreenerResponse = {
  pattern: string;
  total: number;
  scanned: number;
  scanned_at: string;
  items: ScreenerItem[];
};

export type IndexQuote = {
  code: string;
  name: string;
  price: number;
  change_pct: number;
};

export type MarketOverview = {
  indices: IndexQuote[];
  total_amount: number;
  server_time: string;
};

export type WatchlistItem = {
  id: number;
  code: string;
  name: string;
  note: string;
  industry: string;
  market: string;
  price: number;
  change_pct: number;
  volume: number;
  amount: number;
  turnover_rate: number;
  pe: number | null;
  market_cap: number;
  roe: number | null;
  fundamental_status: string;
  fundamental_summary: string;
  concepts: string[];
  sparkline: number[];
  score: number | null;
  pattern: string | null;
  pattern_label: string | null;
  triggers: string[];
  distance_to_support_pct: number | null;
  rr_ratio: number | null;
  support_score: number | null;
  volume_ratio: number | null;
  signal: string;
  signal_label: string;
  signal_reason: string;
  created_at: string;
};

export type WatchlistScore = {
  code: string;
  decision_score: number;
  decision_label: string;
};

export type BacktestTrade = {
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  pnl_pct: number;
  pnl_net: number;
  side: string;
  reason_entry: string;
  reason_exit: string;
  holding_bars: number;
};

export type BacktestResponse = {
  code: string;
  strategy: string;
  period: string;
  trades: BacktestTrade[];
  equity_curve: { date: string; equity: number; benchmark: number }[];
  stats: {
    total_trades: number;
    win_count: number;
    loss_count: number;
    win_rate: number;
    avg_win: number;
    avg_loss: number;
    profit_factor: number;
    max_drawdown: number;
    total_return: number;
    sharpe: number;
    total_cost: number;
  };
  config: Record<string, number | boolean>;
  levels_used: { price: number; kind: string; score: number; label: string }[];
  candles?: Candle[];
};

// ── Algo module types ──

export type AlgoModule = {
  id: string;
  name: string;
  status: string;
  needs_training: boolean;
  description: string;
};

export type OptimizeResult = {
  code: string;
  strategy: string;
  best_params: Record<string, number | boolean>;
  best_value: number;
  default_value: number;
  best_stats: BacktestResponse["stats"];
  default_stats: BacktestResponse["stats"];
  trials_count: number;
};

export type MlTrainResult = {
  samples?: number;
  positive_rate?: number;
  auc?: number;
  accuracy?: number;
  feature_importance?: Record<string, number>;
  error?: string;
};

export type RegimeFitResult = {
  code: string;
  regimes: { id: number; name: string; mean_return: number; mean_vol: number; pct: number }[];
  current_regime: { id: number; name: string };
  regime_sequence: number[];
  transition_matrix: number[][];
  total_bars: number;
};

export type PatternResult = {
  code: string;
  method: string;
  patterns: { pattern_id: number; pattern_name: string; similarity?: number; probability?: number; dtw_distance?: number }[];
};

export type TradeSignal = {
  action: "buy" | "wait" | "near_signal";
  strategy: string;
  reason: string;
  confidence: number;
  current_price: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  risk_pct: number;
  reward_pct: number;
  risk_reward: number;
  nearest_support: number;
  nearest_resistance: number;
  atr: number;
  trend: "up" | "down" | "neutral";
  suggested_position_pct: number;
  factors: string[];
  error?: string;
};

// ── AI Strategy types ──

export type LabeledPoint = {
  idx: number;
  date: string;
  price: number;
  label: "buy" | "sell";
};

export type AiTrainResult = {
  model: string;
  device?: string;
  samples: number;
  class_counts: Record<string, number>;
  accuracy: number;
  buy_precision: number;
  buy_recall: number;
  sell_precision: number;
  sell_recall: number;
  feature_importance?: Record<string, number>;
  epochs?: number;
  train_loss_last?: number;
  elapsed_sec: number;
  codes_used: number;
  error?: string;
};

export type AiPrediction = {
  code: string;
  hold_prob: number;
  buy_prob: number;
  sell_prob: number;
  action: "hold" | "buy" | "sell";
  confidence: number;
  error?: string;
};

export type AiSignal = {
  code: string;
  action: "buy" | "sell" | "hold";
  model_type: string;
  reason: string;
  confidence: number;
  current_price: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  risk_pct: number;
  reward_pct: number;
  risk_reward: number;
  nearest_support: number;
  nearest_resistance: number;
  atr: number;
  trend: string;
  suggested_position_pct: number;
  factors: string[];
  probabilities: { hold: number; buy: number; sell: number };
  candles?: Candle[];
  error?: string;
};

export type AiBacktestResult = {
  code: string;
  model_type: string;
  candles?: Candle[];
  trades: {
    entry_date: string;
    entry_price: number;
    exit_date: string;
    exit_price: number;
    pnl_pct: number;
    pnl_net: number;
    holding_bars: number;
    reason_exit: string;
  }[];
  equity_curve: { date: string; equity: number; benchmark: number }[];
  stats: {
    total_trades: number;
    win_count: number;
    loss_count: number;
    win_rate: number;
    avg_win: number;
    avg_loss: number;
    profit_factor: number;
    max_drawdown: number;
    total_return: number;
    benchmark_return: number;
    sharpe: number;
  };
  error?: string;
};

export type AiModelStatus = {
  lightgbm: { trained: boolean; path: string | null };
  transformer: { trained: boolean; path: string | null };
  lstm: { trained: boolean; path: string | null };
  cnn_lstm: { trained: boolean; path: string | null };
  rl_ppo: { trained: boolean; path: string | null };
};

// ── Recommender (v2 strategy) ────────────────────────────────
export type RecommendStyle = "short_term" | "swing" | "value" | "multi_factor" | "ai_ensemble";

export type TradePlan = {
  buy_low: number;
  buy_high: number;
  buy_trigger: string;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  position_pct: number;          // 0–1
  holding_days_min: number;
  holding_days_max: number;
  risk_reward: number;
  atr_pct: number;
  confidence: number;
  reason: string;
  factors: Record<string, number | string | null>;
  // New tradability fields
  state?: "buy" | "wait_breakout" | "wait_pullback" | "reject";
  tradable?: boolean;
  risk_warning?: string;
};

export type Recommendation = {
  id?: number;
  code: string;
  name: string;
  style: RecommendStyle;
  score: number;
  rank?: number;
  tier?: "core" | "watch" | "observe";
  price: number;
  industry: string;
  concept: string;
  reasons: string[];
  factors: Record<string, number>;
  scan_date?: string;
  expires_date?: string;
  status?: string;
  plan: TradePlan | null;
};

export type RecommendListResp = {
  scan_date: string;
  style: RecommendStyle | null;
  count: number;
  items: Recommendation[];
};

export type RecommendScanProgress = {
  scan_id: string;
  styles: RecommendStyle[];
  status: "running" | "done" | "error";
  phase: string;
  pct: number;
  processed?: number;
  total?: number;
  counts?: Record<RecommendStyle, number>;
  error?: string;
};

export type MlModelKey = "lgbm" | "seq" | "rl";

export type MlTrainProgress = {
  job_id: string;
  model: MlModelKey | string;
  params?: Record<string, unknown>;
  status: "running" | "done" | "error";
  phase: string;
  pct: number;
  // Per-phase fields (any of these may be populated)
  snap_dates_done?: number;
  snap_dates_total?: number;
  samples?: number;
  train_samples?: number;
  val_samples?: number;
  epoch?: number;
  epochs?: number;
  train_loss?: number;
  val_loss?: number;
  val_ic?: number;
  timesteps?: number;
  total_timesteps?: number;
  // Final
  meta?: Record<string, unknown>;
  error?: string;
  started_at?: string;
  finished_at?: string;
};

export type MlRegistry = Record<
  string,
  null | {
    model: string;
    saved_at?: string;
    val_rank_ic?: number;
    final_val_ic?: number;
    eval_avg_reward?: number;
    samples_train?: number;
    samples_val?: number;
    top_features?: [string, number][];
    [k: string]: unknown;
  }
>;

export type MlTrainStatusResp = {
  current: ({ running: boolean } & Partial<MlTrainProgress>) | null;
  registry: MlRegistry;
};

// ── Dragon Strategy (龙头战法) ──
export type DragonStatus = {
  stage1: { trained: boolean; modified_at: string | null; size_kb: number };
  stage2: { trained: boolean; modified_at: string | null; size_kb: number };
};

export type MarketCycle = {
  trade_date: string;
  phase: "ice" | "warmup" | "peak" | "cooldown";
  score: number;
  zt_count: number;
  blast_count: number;
  blast_rate: number;
  high_consecutive: number;
  consecutive_3plus: number;
  yesterday_zt_today_perf: number;
};

export type ZtPoolItem = {
  code: string;
  name: string;
  change_pct: number | null;
  close: number | null;
  amount: number | null;
  market_cap: number | null;
  turnover_rate: number | null;
  first_zt_time: string;
  last_zt_time: string;
  open_count: number;
  seal_amount: number | null;
  consecutive: number;
  concept: string;
  industry: string;
  zt_status: string;
};

export type LhbRecord = {
  trade_date: string;
  name: string;
  reason: string;
  close: number | null;
  change_pct: number | null;
  turnover: number | null;
  buy_total: number;
  sell_total: number;
  net_amount: number;
  net_rate: number | null;
};

export type LhbSeat = {
  rank: number;
  side: "buy" | "sell";
  seat_name: string;
  buy_amount: number;
  sell_amount: number;
  net_amount: number;
  is_known_hot: boolean;
  hot_money_tag: string;
};

export type BoardHeatItem = {
  concept: string;
  board_code: string;
  change_pct: number | null;
  net_inflow: number | null;
  heat_score: number | null;
  heat_level: string;
  rank: number | null;
  zt_count: number;
  up_ratio: number | null;
  leader_code: string;
  leader_name: string;
  leader_change: number | null;
  leader_consecutive: number;
  trend: { date: string; score: number | null }[];
};

export type DragonTrainJob = {
  task_id: string;
  status: string;
  progress: number;
  message: string;
  start_date: string;
  end_date: string;
  epochs: number;
  train_stage2: boolean;
  started_at: number;
  ended_at: number | null;
  result: Record<string, unknown> | null;
};

export type DragonScanCandidate = {
  code: string;
  name: string;
  trade_date: string;
  signal_type: "buy" | "sell" | "hold";
  dragon_score: number;
  dragon_rank: number;
  consecutive: number;
  concept: string;
  market_cycle: string;
  model_confidence: number;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  reasons: string[];
  feature_snapshot: Record<string, unknown>;
  amount?: number;
  industry?: string;
};

export type DragonScanJob = {
  task_id: string;
  status: string;
  progress: number;
  message: string;
  date: string;
  threshold: number;
  started_at: number;
  ended_at: number | null;
  candidates: DragonScanCandidate[];
};

export type DragonSignalRow = {
  code: string;
  name: string;
  signal_type: string;
  dragon_rank: number;
  dragon_score: number;
  concept: string;
  consecutive: number;
  model_conf: number;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  market_cycle: string;
  reason: Record<string, unknown>;
};

export type DragonSignalDetail = DragonScanCandidate;

export type DragonBacktestResult = {
  start_date: string;
  end_date: string;
  init_cash: number;
  final_cash: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  trades: number;
  win_rate: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  params: Record<string, unknown>;
  equity_curve: { date: string; equity: number }[];
  trade_list: {
    code: string;
    entry_date: string;
    exit_date: string;
    entry_price: number;
    exit_price: number;
    pnl_pct: number;
    reason: string;
    dragon_score: number;
  }[];
  error?: string;
};
