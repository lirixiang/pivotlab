export type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
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
  concepts: string[];
  fundamentals: FundamentalSnapshot | null;
  analyst_consensus: AnalystConsensus | null;
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
  price: number;
  change_pct: number;
  volume: number;
  amount: number;
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
