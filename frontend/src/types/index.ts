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
  };
  levels_used: { price: number; kind: string; score: number; label: string }[];
};
