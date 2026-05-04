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
  created_at: string;
};
