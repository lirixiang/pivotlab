import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { BacktestResponse, BacktestTrade } from "../types";

const PERIODS = [
  { k: "1m", l: "近1月" },
  { k: "3m", l: "近3月" },
  { k: "6m", l: "近6月" },
  { k: "1y", l: "近1年" },
];

export function BacktestPage({ defaultCode }: { defaultCode: string }) {
  const [code, setCode] = useState(defaultCode);
  const [period, setPeriod] = useState("3m");
  const [strategy, setStrategy] = useState("breakout_pullback");
  const [stopLoss, setStopLoss] = useState(2.5);
  const [target, setTarget] = useState(6);
  const [volumeFilter, setVolumeFilter] = useState(true);
  const [shrinkFilter, setShrinkFilter] = useState(true);
  const [closeAbove, setCloseAbove] = useState(true);
  const [weeklyConf, setWeeklyConf] = useState(true);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [stockName, setStockName] = useState("");

  // Stock search
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<{ code: string; name: string; industry: string }[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setSearchResults([]); return; }
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      api.searchStocks(q, 10).then((r) => {
        setSearchResults(r);
        setShowDropdown(true);
      }).catch(() => {});
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [query]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setShowDropdown(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const pickStock = (c: string, name: string) => {
    setCode(c);
    setStockName(name);
    setQuery("");
    setShowDropdown(false);
  };

  const runBacktest = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.backtest({
        code,
        strategy,
        period,
        stop_loss: stopLoss,
        target,
        volume_filter: volumeFilter,
        shrink_filter: shrinkFilter,
        close_above_support: closeAbove,
        weekly_confluence: weeklyConf,
      });
      setResult(res);
      if (!stockName) setStockName(code);
    } finally {
      setLoading(false);
    }
  }, [code, strategy, period, stopLoss, target, volumeFilter, shrinkFilter, closeAbove, weeklyConf, stockName]);

  // Auto-run on first mount
  useEffect(() => {
    if (code) runBacktest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stats = result?.stats;
  const trades = result?.trades ?? [];
  const curve = result?.equity_curve ?? [];

  return (
    <div className="flex-1 grid" style={{ gridTemplateColumns: "260px 1fr 320px" }}>
      {/* ── Left sidebar: Settings ── */}
      <aside className="border-r border-ink-700 bg-ink-900 p-4 overflow-y-auto scrollbar">
        <div className="tag text-ink-500 mb-3">回测设置</div>

        <Block label="标的">
          <div ref={wrapRef} className="relative">
            <div className="flex gap-1.5">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onFocus={() => query.trim() && setShowDropdown(true)}
                placeholder={code ? `${code} ${stockName}` : "输入代码或名称搜索"}
                className="flex-1 bg-ink-850 border border-ink-700 rounded-md px-3 py-2 text-[12px] focus:outline-none focus:border-gold/60 placeholder:text-ink-500"
              />
            </div>
            {code && !query && (
              <div className="mt-1.5 flex items-center gap-1.5">
                <span className="text-[12px] text-gold num">{code}</span>
                <span className="text-[12px] text-ink-300">{stockName}</span>
              </div>
            )}
            {showDropdown && searchResults.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-ink-850 border border-ink-700 rounded-md shadow-xl z-50 max-h-[240px] overflow-y-auto scrollbar">
                {searchResults.map((s) => (
                  <button
                    key={s.code}
                    onClick={() => pickStock(s.code, s.name)}
                    className="w-full text-left px-3 py-2 hover:bg-ink-800 flex justify-between items-baseline text-[12px] border-b border-ink-800/50 last:border-0"
                  >
                    <div>
                      <span className="text-ink-100">{s.name}</span>
                      <span className="text-ink-500 num ml-2">{s.code}</span>
                    </div>
                    <span className="text-[10px] text-ink-500">{s.industry}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </Block>

        <Block label="策略">
          <div className="space-y-1">
            {[
              { k: "breakout_pullback", l: "突破回踩 入场", c: "gold" },
              { k: "bottom_stabilize", l: "下跌企稳 入场", c: "sky" },
            ].map((o) => (
              <button
                key={o.k}
                onClick={() => setStrategy(o.k)}
                className={
                  "w-full flex items-center gap-2 px-3 py-2 rounded-md text-[12px] " +
                  (strategy === o.k
                    ? "bg-ink-800 text-white ring-soft"
                    : "text-ink-300 hover:bg-ink-850")
                }
              >
                <span className={"dot " + (o.c === "gold" ? "bg-gold" : "bg-sky2")} />
                {o.l}
              </button>
            ))}
          </div>
        </Block>

        <Block label="时间区间">
          <div className="grid grid-cols-2 gap-1.5">
            {PERIODS.map((p) => (
              <button
                key={p.k}
                onClick={() => setPeriod(p.k)}
                className={
                  "px-2 py-1.5 rounded-md text-[12px] " +
                  (period === p.k
                    ? "bg-ink-800 text-white ring-soft"
                    : "text-ink-400 bg-ink-850 hover:text-white")
                }
              >
                {p.l}
              </button>
            ))}
          </div>
        </Block>

        <Block label="止损 / 目标">
          <div className="grid grid-cols-2 gap-2 text-[12px]">
            <label className="bg-ink-850 ring-soft rounded-md px-2 py-1.5 flex flex-col">
              <span className="text-[10px] text-ink-500">止损 %</span>
              <input
                value={stopLoss}
                onChange={(e) => setStopLoss(Number(e.target.value) || 2.5)}
                type="number" step="0.5"
                className="bg-transparent num text-[13px] text-ink-100 focus:outline-none w-full"
              />
            </label>
            <label className="bg-ink-850 ring-soft rounded-md px-2 py-1.5 flex flex-col">
              <span className="text-[10px] text-ink-500">目标 %</span>
              <input
                value={target}
                onChange={(e) => setTarget(Number(e.target.value) || 6)}
                type="number" step="0.5"
                className="bg-transparent num text-[13px] text-ink-100 focus:outline-none w-full"
              />
            </label>
          </div>
        </Block>

        <Block label="过滤">
          <div className="space-y-1.5 text-[12px] text-ink-300">
            <Toggle label="放量突破（量比 ≥1.5）" value={volumeFilter} onChange={setVolumeFilter} />
            <Toggle label="缩量回踩 ≤ 突破量 50%" value={shrinkFilter} onChange={setShrinkFilter} />
            <Toggle label="收盘价站稳支撑位" value={closeAbove} onChange={setCloseAbove} />
            <Toggle label="多周期共振（日+周）" value={weeklyConf} onChange={setWeeklyConf} />
          </div>
        </Block>

        <button
          className="w-full grad-gold text-ink-950 font-semibold py-2 rounded-md text-[13px] disabled:opacity-50"
          onClick={runBacktest}
          disabled={loading || !code}
        >
          {loading ? (
            <><i className="fas fa-circle-notch fa-spin mr-1" /> 回测中...</>
          ) : (
            <><i className="fas fa-flask-vial mr-1" /> 运行回测</>
          )}
        </button>
      </aside>

      {/* ── Center: Results ── */}
      <section className="bg-ink-950 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head">
          <div>
            <h2 className="text-[15px] font-semibold text-white">
              {stockName || code || "—"}
              <span className="text-ink-500 num text-sm ml-2">{code}</span>
            </h2>
            <div className="text-[11px] text-ink-500 mt-0.5">
              策略：{strategy === "breakout_pullback" ? "突破回踩" : "下跌企稳"} · 区间：
              {PERIODS.find((p) => p.k === period)?.l}
              {stats ? ` · ${stats.total_trades} 笔交易` : ""}
            </div>
          </div>
          {result && (
            <div className="flex items-center gap-2 text-[11px]">
              <Pill label="支撑位" value={result.levels_used.filter((l) => l.kind === "support").length} />
              <Pill label="压力位" value={result.levels_used.filter((l) => l.kind === "resistance").length} />
            </div>
          )}
        </div>

        {stats && (
          <div className="grid grid-cols-5 gap-3 p-5 border-b border-ink-800">
            <Metric
              label="累计收益率"
              value={(stats.total_return >= 0 ? "+" : "") + stats.total_return.toFixed(1) + "%"}
              color={stats.total_return >= 0 ? "text-cn-up" : "text-cn-dn"}
            />
            <Metric
              label="胜率"
              value={(stats.win_rate * 100).toFixed(1) + "%"}
              color={stats.win_rate >= 0.5 ? "text-cn-up" : "text-cn-dn"}
            />
            <Metric label="盈亏比" value={String(stats.profit_factor)} color="text-gold" />
            <Metric
              label="最大回撤"
              value={stats.max_drawdown.toFixed(1) + "%"}
              color="text-cn-dn"
            />
            <Metric
              label="交易次数"
              value={String(stats.total_trades)}
              color="text-ink-100"
            />
          </div>
        )}

        <div className="px-5 py-4 flex-1 flex flex-col min-h-0">
          <div className="flex items-center justify-between mb-3">
            <span className="tag text-ink-500">资金曲线</span>
            <div className="flex items-center gap-4 text-[11px] text-ink-500">
              <span className="flex items-center gap-1.5">
                <span className="dot bg-gold" /> 策略净值
              </span>
              <span className="flex items-center gap-1.5">
                <span className="dot bg-ink-500" /> 基准（买入持有）
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 bg-cn-up rounded-sm inline-block" /> 买入
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 bg-cn-dn rounded-sm inline-block" /> 卖出
              </span>
            </div>
          </div>
          <div className="flex-1 min-h-[260px] bg-ink-900 ring-soft rounded-lg p-3">
            {loading ? (
              <div className="h-full flex items-center justify-center text-ink-500 text-sm">
                <i className="fas fa-circle-notch fa-spin mr-2" /> 计算中...
              </div>
            ) : curve.length > 0 ? (
              <EquityChart points={curve} trades={trades} />
            ) : (
              <div className="h-full flex items-center justify-center text-ink-600 text-sm">
                点击「运行回测」开始
              </div>
            )}
          </div>

          {stats && (
            <div className="grid grid-cols-3 gap-3 mt-4">
              <Stat2
                label="平均盈利"
                value={`+${stats.avg_win.toFixed(2)}%`}
                color="text-cn-up"
              />
              <Stat2
                label="平均亏损"
                value={`${stats.avg_loss.toFixed(2)}%`}
                color="text-cn-dn"
              />
              <Stat2
                label="胜 / 负"
                value={`${stats.win_count} / ${stats.loss_count}`}
                color="text-ink-100"
              />
            </div>
          )}
        </div>
      </section>

      {/* ── Right sidebar: Trade list ── */}
      <aside className="border-l border-ink-700 bg-ink-900 overflow-y-auto scrollbar flex flex-col">
        <div className="p-4 border-b border-ink-800">
          <div className="tag text-ink-500 mb-1">交易明细</div>
          <div className="text-[11px] text-ink-500">
            共 {trades.length} 笔交易
            {stats ? ` · 胜率 ${(stats.win_rate * 100).toFixed(0)}%` : ""}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar">
          {trades.length === 0 && !loading && (
            <div className="text-[12px] text-ink-500 text-center py-8">
              <i className="fas fa-chart-line text-2xl text-ink-700 block mb-2" />
              暂无交易记录
            </div>
          )}
          {trades.map((t, i) => (
            <TradeRow key={i} trade={t} index={i + 1} />
          ))}
        </div>
        {result && result.levels_used.length > 0 && (
          <div className="border-t border-ink-800 p-4">
            <div className="tag text-ink-500 mb-2">使用的关键位</div>
            <div className="space-y-1">
              {result.levels_used.map((l, i) => (
                <div key={i} className="flex items-center justify-between text-[11px]">
                  <span className={l.kind === "support" ? "text-cn-up" : "text-cn-dn"}>
                    {l.label} {l.price}
                  </span>
                  <span className="text-ink-500 num">评分 {l.score}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

/* ── TradeRow ── */
function TradeRow({ trade: t, index }: { trade: BacktestTrade; index: number }) {
  const win = t.pnl_pct > 0;
  const exitLabel: Record<string, string> = {
    target: "止盈",
    stop: "止损",
    timeout: "超时",
    open: "持仓中",
  };
  return (
    <div className="px-4 py-2.5 border-b border-ink-850/60 row-hover">
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-[11px] text-ink-500">#{index}</span>
        <span className={
          "num text-[13px] font-medium " + (win ? "text-cn-up" : "text-cn-dn")
        }>
          {win ? "+" : ""}{t.pnl_pct.toFixed(2)}%
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 text-[11px]">
        <div>
          <div className="text-ink-500">买入</div>
          <div className="text-cn-up num">{t.entry_price}</div>
          <div className="text-ink-500 text-[10px]">{fmtDate(t.entry_date)}</div>
        </div>
        <div>
          <div className="text-ink-500">卖出</div>
          <div className="text-cn-dn num">{t.exit_price}</div>
          <div className="text-ink-500 text-[10px]">{fmtDate(t.exit_date)}</div>
        </div>
      </div>
      <div className="flex items-center justify-between mt-1.5 text-[10px]">
        <span className="text-ink-500">{t.reason_entry}</span>
        <div className="flex items-center gap-2">
          <span className="text-ink-500">持仓 {t.holding_bars} 天</span>
          <span className={
            "px-1.5 py-0.5 rounded text-[9px] " +
            (t.reason_exit === "target" ? "bg-cn-up/20 text-cn-up" :
             t.reason_exit === "stop" ? "bg-cn-dn/20 text-cn-dn" :
             "bg-ink-750 text-ink-400")
          }>
            {exitLabel[t.reason_exit] || t.reason_exit}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ── Equity Chart with trade markers ── */
function EquityChart({
  points,
  trades,
}: {
  points: { date: string; equity: number; benchmark: number }[];
  trades: BacktestTrade[];
}) {
  const W = 800;
  const H = 260;
  const ys = points.flatMap((p) => [p.equity - 100, p.benchmark]);
  const minY = Math.min(...ys, 0) - 1;
  const maxY = Math.max(...ys, 0) + 1;
  const xScale = (i: number) => (i / Math.max(1, points.length - 1)) * W;
  const yScale = (v: number) => H - ((v - minY) / (maxY - minY)) * H;

  const strategyPath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(p.equity - 100).toFixed(1)}`)
    .join(" ");
  const benchPath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(p.benchmark).toFixed(1)}`)
    .join(" ");

  // Map trade dates to point indices
  const dateMap = new Map(points.map((p, i) => [p.date, i]));
  const entryMarkers: { x: number; y: number; trade: BacktestTrade }[] = [];
  const exitMarkers: { x: number; y: number; trade: BacktestTrade }[] = [];
  for (const t of trades) {
    const ei = dateMap.get(t.entry_date);
    const xi = dateMap.get(t.exit_date);
    if (ei !== undefined) {
      entryMarkers.push({ x: xScale(ei), y: yScale(points[ei].equity - 100), trade: t });
    }
    if (xi !== undefined) {
      exitMarkers.push({ x: xScale(xi), y: yScale(points[xi].equity - 100), trade: t });
    }
  }

  // X-axis labels (show ~6 dates)
  const step = Math.max(1, Math.floor(points.length / 6));
  const xLabels = points.filter((_, i) => i % step === 0 || i === points.length - 1);

  return (
    <div className="w-full h-full relative">
      <svg viewBox={`0 0 ${W} ${H + 24}`} className="w-full h-full" preserveAspectRatio="none">
        <defs>
          <linearGradient id="bt-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#d4a857" stopOpacity="0.2" />
            <stop offset="100%" stopColor="#d4a857" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* zero line */}
        <line x1={0} x2={W} y1={yScale(0)} y2={yScale(0)} stroke="#262d3d" strokeDasharray="3 4" />
        {/* Y-axis labels */}
        {[minY, 0, maxY].map((v) => (
          <text key={v} x={4} y={yScale(v) - 3} fill="#4a5568" fontSize="9" fontFamily="monospace">
            {v >= 0 ? "+" : ""}{v.toFixed(1)}%
          </text>
        ))}
        {/* benchmark */}
        <path d={benchPath} fill="none" stroke="#3a4254" strokeWidth="1.2" />
        {/* strategy area */}
        <path
          d={`${strategyPath} L ${W} ${H} L 0 ${H} Z`}
          fill="url(#bt-area)"
        />
        {/* strategy line */}
        <path d={strategyPath} fill="none" stroke="#d4a857" strokeWidth="1.8" />
        {/* entry markers */}
        {entryMarkers.map((m, i) => (
          <g key={`e${i}`}>
            <polygon
              points={`${m.x},${m.y - 4} ${m.x - 4},${m.y + 4} ${m.x + 4},${m.y + 4}`}
              fill="#ef4444"
              opacity="0.9"
            />
            <title>买入 {m.trade.entry_price} ({fmtDate(m.trade.entry_date)}) {m.trade.reason_entry}</title>
          </g>
        ))}
        {/* exit markers */}
        {exitMarkers.map((m, i) => (
          <g key={`x${i}`}>
            <polygon
              points={`${m.x},${m.y + 4} ${m.x - 4},${m.y - 4} ${m.x + 4},${m.y - 4}`}
              fill="#22c55e"
              opacity="0.9"
            />
            <title>卖出 {m.trade.exit_price} ({fmtDate(m.trade.exit_date)}) {m.trade.pnl_pct > 0 ? "+" : ""}{m.trade.pnl_pct}%</title>
          </g>
        ))}
        {/* x-axis date labels */}
        {xLabels.map((p, i) => {
          const idx = points.indexOf(p);
          return (
            <text
              key={i}
              x={xScale(idx)}
              y={H + 16}
              fill="#4a5568"
              fontSize="9"
              fontFamily="monospace"
              textAnchor="middle"
            >
              {fmtDate(p.date)}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

/* ── Helpers ── */
function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <div className="text-[11px] text-ink-400 mb-2">{label}</div>
      {children}
    </div>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!value)}
      className="w-full flex items-center justify-between px-3 py-1.5 rounded-md hover:bg-ink-850"
    >
      <span>{label}</span>
      <span className={"w-8 h-4 rounded-full relative transition " + (value ? "bg-gold/60" : "bg-ink-700")}>
        <span className={"absolute top-0.5 w-3 h-3 rounded-full bg-white transition " + (value ? "left-4" : "left-0.5")} />
      </span>
    </button>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-ink-900 ring-soft rounded-lg p-3">
      <div className="text-[10px] text-ink-500 tag">{label}</div>
      <div className={"num text-xl mt-1 " + color}>{value}</div>
    </div>
  );
}

function Stat2({ label, value, color = "text-ink-100" }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-ink-900 ring-soft rounded-lg p-3">
      <div className="text-[10px] text-ink-500 tag">{label}</div>
      <div className={"num mt-1 " + color}>{value}</div>
    </div>
  );
}

function Pill({ label, value }: { label: string; value: number }) {
  return (
    <span className="chip">
      {label} <span className="num text-ink-100 ml-1">{value}</span>
    </span>
  );
}

function fmtDate(d: string) {
  // "2025-01-15" -> "01-15", "bar_123" -> "—"
  if (!d || d.startsWith("bar_")) return "—";
  const parts = d.split("-");
  if (parts.length >= 3) return `${parts[1]}-${parts[2].slice(0, 2)}`;
  return d.slice(0, 10);
}
