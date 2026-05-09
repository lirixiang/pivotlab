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
  const [metric, setMetric] = useState<"eps" | "revenue" | "net_profit" | "roe" | "net_profit_yoy" | "revenue_yoy">("net_profit_yoy");

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
          <option value="net_profit_yoy">净利润增长</option>
          <option value="revenue">营收</option>
          <option value="revenue_yoy">营收增长</option>
          <option value="eps">EPS</option>
          <option value="roe">ROE</option>
        </select>
      </div>

      {/* Mini bar chart */}
      <MiniBarChart items={items} metric={metric} />

      {/* YoY growth trend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px]">
        {items.slice(-8).map((d) => {
          const yoyKey = metric === "revenue" || metric === "revenue_yoy" ? "revenue_yoy" : "net_profit_yoy";
          const yoy = d[yoyKey];
          return (
            <div key={d.period} className="flex items-center gap-1">
              <span className="text-ink-600">{fmtQuarter(d.period)}</span>
              <span className={yoy >= 0 ? "text-green-400" : "text-red-400"}>
                {yoy >= 0 ? "+" : ""}{yoy.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}

function MiniBarChart({ items, metric }: { items: HistoryItem[]; metric: "eps" | "revenue" | "net_profit" | "roe" | "net_profit_yoy" | "revenue_yoy" }) {
  const values = items.map((d) => d[metric]);
  const max = Math.max(...values.map(Math.abs), 1e-9);
  const hasNeg = values.some((v) => v < 0);

  const isYoy = metric === "net_profit_yoy" || metric === "revenue_yoy";
  const isAmt = metric === "revenue" || metric === "net_profit";
  const unit = isYoy ? "%" : metric === "roe" ? "%" : isAmt ? "亿" : "";

  return (
    <div className="w-full">
      <div className={"flex gap-[2px] h-16" + (hasNeg ? "" : " items-end")}>
        {items.map((d, i) => {
          const val = d[metric];
          const pct = Math.abs(val) / max;
          const positive = val >= 0;
          const barH = `${Math.max(pct * (hasNeg ? 50 : 100), 2)}%`;
          return (
            <div
              key={d.period}
              className={"flex-1 flex flex-col items-center group relative" + (hasNeg ? "" : " justify-end")}
              style={hasNeg ? { height: "100%", display: "flex", flexDirection: "column" } : { height: "100%" }}
            >
              {/* Tooltip */}
              <div className="absolute bottom-full mb-1 hidden group-hover:block z-10 bg-ink-800 rounded px-1.5 py-0.5 text-[9px] text-ink-200 whitespace-nowrap shadow-lg">
                {fmtQuarter(d.period)}: {isYoy ? `${val >= 0 ? "+" : ""}${val.toFixed(1)}` : isAmt ? fmtAmt(val) : val.toFixed(2)}{unit}
              </div>
              {hasNeg ? (
                <>
                  {/* Upper half: positive bars grow upward from center */}
                  <div className="flex-1 flex items-end w-full">
                    {positive && (
                      <div
                        className="w-full rounded-t-sm bg-green-500/70 hover:opacity-80"
                        style={{ height: barH }}
                      />
                    )}
                  </div>
                  {/* Lower half: negative bars grow downward from center */}
                  <div className="flex-1 flex items-start w-full">
                    {!positive && (
                      <div
                        className="w-full rounded-b-sm bg-red-500/70 hover:opacity-80"
                        style={{ height: barH }}
                      />
                    )}
                  </div>
                </>
              ) : (
                <div
                  className={`w-full rounded-t-sm transition-all ${
                    positive ? "bg-green-500/70" : "bg-red-500/70"
                  } hover:opacity-80`}
                  style={{ height: barH }}
                />
              )}
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
