import { useEffect, useState } from "react";
import { api } from "../services/api";

type HistoryItem = {
  period: string;
  eps: number;
  roe: number;
  revenue: number;
  net_profit: number;
  revenue_yoy: number;
  net_profit_yoy: number;
};

function fmtAmt(v: number) {
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(1)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
  return v.toFixed(0);
}

function fmtQuarter(period: string) {
  // "2025-03-31" → "25Q1"
  const [y, m] = period.split("-");
  const q = Math.ceil(Number(m) / 3);
  return `${y.slice(2)}Q${q}`;
}

export function FinancialHistoryPanel({ code }: { code: string }) {
  const [data, setData] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [metric, setMetric] = useState<"eps" | "revenue" | "net_profit" | "roe">("net_profit");

  useEffect(() => {
    if (!code) return;
    setLoading(true);
    api.financialHistory(code)
      .then((r) => setData(r.history))
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, [code]);

  if (loading) {
    return (
      <div className="text-[11px] text-ink-500 py-2">
        <i className="fas fa-spinner fa-spin mr-1" /> 加载历史业绩...
      </div>
    );
  }
  if (!data.length) return null;

  // Last 20 quarters (5 years)
  const items = data.slice(-20);

  return (
    <>
      <div className="border-t border-ink-800" />
      <div className="text-ink-300 font-medium text-[11px] flex items-center justify-between">
        <span>
          <i className="fas fa-chart-bar mr-1 text-[10px] text-amber-400" />
          历史业绩 ({items.length}季)
        </span>
        <select
          className="bg-ink-800 border-0 text-[10px] text-ink-400 rounded px-1 py-0.5"
          value={metric}
          onChange={(e) => setMetric(e.target.value as typeof metric)}
        >
          <option value="net_profit">净利润</option>
          <option value="revenue">营收</option>
          <option value="eps">EPS</option>
          <option value="roe">ROE</option>
        </select>
      </div>

      {/* Mini bar chart */}
      <MiniBarChart items={items} metric={metric} />

      {/* YoY growth trend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px]">
        {items.slice(-8).map((d) => (
          <div key={d.period} className="flex items-center gap-1">
            <span className="text-ink-600">{fmtQuarter(d.period)}</span>
            <span className={d.net_profit_yoy >= 0 ? "text-green-400" : "text-red-400"}>
              {d.net_profit_yoy >= 0 ? "+" : ""}{d.net_profit_yoy.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

function MiniBarChart({ items, metric }: { items: HistoryItem[]; metric: "eps" | "revenue" | "net_profit" | "roe" }) {
  const values = items.map((d) => d[metric]);
  const max = Math.max(...values.map(Math.abs), 1e-9);
  const hasNeg = values.some((v) => v < 0);

  // For revenue/net_profit, show in 亿
  const isAmt = metric === "revenue" || metric === "net_profit";
  const unit = metric === "roe" ? "%" : isAmt ? "亿" : "";

  return (
    <div className="w-full">
      <div className="flex items-end gap-[2px] h-16">
        {items.map((d, i) => {
          const val = d[metric];
          const pct = Math.abs(val) / max;
          const positive = val >= 0;
          return (
            <div
              key={d.period}
              className="flex-1 flex flex-col justify-end items-center group relative"
              style={{ height: "100%" }}
            >
              {/* Tooltip */}
              <div className="absolute bottom-full mb-1 hidden group-hover:block z-10 bg-ink-800 rounded px-1.5 py-0.5 text-[9px] text-ink-200 whitespace-nowrap shadow-lg">
                {fmtQuarter(d.period)}: {isAmt ? fmtAmt(val) : val.toFixed(2)}{unit}
              </div>
              <div
                className={`w-full rounded-t-sm transition-all ${
                  positive ? "bg-green-500/70" : "bg-red-500/70"
                } hover:opacity-80`}
                style={{
                  height: `${Math.max(pct * (hasNeg ? 50 : 100), 2)}%`,
                  marginTop: hasNeg && positive ? "auto" : undefined,
                }}
              />
            </div>
          );
        })}
      </div>
      {/* X axis labels (show every 4th) */}
      <div className="flex gap-[2px] mt-0.5">
        {items.map((d, i) => (
          <div key={d.period} className="flex-1 text-center text-[8px] text-ink-600 overflow-hidden">
            {i % 4 === 0 ? fmtQuarter(d.period) : ""}
          </div>
        ))}
      </div>
    </div>
  );
}
