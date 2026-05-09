import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import { TradeChart } from "../components/TradeChart";
import type { BacktestResponse, BacktestTrade, Candle, OptimizeResult, TradeSignal } from "../types";

const PERIODS = [
  { k: "1m", l: "1M" },
  { k: "3m", l: "3M" },
  { k: "6m", l: "6M" },
  { k: "1y", l: "1Y" },
];

export function BacktestPage({ defaultCode }: { defaultCode: string }) {
  const [code, setCode] = useState(defaultCode);
  const [period, setPeriod] = useState("3m");
  const [strategy, setStrategy] = useState("breakout_pullback");
  const [stopLoss, setStopLoss] = useState(2.5);
  const [target, setTarget] = useState(6);
  const [maxHold, setMaxHold] = useState(20);
  const [useAtrStop, setUseAtrStop] = useState(false);
  const [atrStopMult, setAtrStopMult] = useState(2.0);
  const [volumeFilter, setVolumeFilter] = useState(true);
  const [shrinkFilter, setShrinkFilter] = useState(true);
  const [closeAbove, setCloseAbove] = useState(true);
  const [weeklyConf, setWeeklyConf] = useState(true);
  const [maTrend, setMaTrend] = useState(false);
  const [maPeriod, setMaPeriod] = useState(20);
  const [pullbackMin, setPullbackMin] = useState(0.1);
  const [pullbackMax, setPullbackMax] = useState(3.0);
  const [minScore, setMinScore] = useState(30);
  const [stabilizeBars, setStabilizeBars] = useState(3);
  const [stabilizeDist, setStabilizeDist] = useState(3.0);
  const [commission, setCommission] = useState(0.1);
  const [slippage, setSlippage] = useState(0.05);
  const [cooldown, setCooldown] = useState(2);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [stockName, setStockName] = useState("");
  const [optimizing, setOptimizing] = useState(false);
  const [optResult, setOptResult] = useState<OptimizeResult | null>(null);
  const [optTarget, setOptTarget] = useState<"sharpe" | "return" | "calmar">("sharpe");
  const [signal, setSignal] = useState<TradeSignal | null>(null);
  const [sigLoading, setSigLoading] = useState(false);
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);

  const resolveStockName = useCallback((c: string) => {
    if (!c) return;
    api.searchStocks(c, 1).then((r) => {
      const match = r.find((s) => s.code === c);
      if (match) setStockName(match.name);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (defaultCode && !stockName) resolveStockName(defaultCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultCode]);

  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<{ code: string; name: string; industry: string }[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setSearchResults([]); return; }
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      api.searchStocks(q, 10).then((r) => { setSearchResults(r); setShowDropdown(true); }).catch(() => {});
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [query]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) setShowDropdown(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const pickStock = (c: string, name: string) => {
    setCode(c); setStockName(name); setQuery(""); setShowDropdown(false);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (searchResults.length > 0) {
        pickStock(searchResults[0].code, searchResults[0].name);
      } else if (query.trim().match(/^\d{6}$/)) {
        const c = query.trim();
        setCode(c); setStockName(""); resolveStockName(c); setQuery(""); setShowDropdown(false);
      }
    } else if (e.key === "Escape") { setShowDropdown(false); setQuery(""); }
  };

  const runBacktest = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        code, strategy, period, stop_loss: stopLoss, target, max_hold_bars: maxHold,
        use_atr_stop: useAtrStop, atr_stop_mult: atrStopMult,
        volume_filter: volumeFilter, shrink_filter: shrinkFilter,
        close_above_support: closeAbove, weekly_confluence: weeklyConf,
        ma_trend_filter: maTrend, ma_trend_period: maPeriod,
        pullback_min_pct: pullbackMin, pullback_max_pct: pullbackMax,
        min_level_score: minScore, stabilize_bars: stabilizeBars,
        stabilize_max_dist_pct: stabilizeDist,
        commission_pct: commission, slippage_pct: slippage, cooldown_bars: cooldown,
      };
      const res = await api.backtest(params);
      setResult(res); setSelectedTrade(null);
      if (!stockName) resolveStockName(code);
      setSigLoading(true);
      api.signal({ ...params, backtest_stats: res.stats })
        .then(setSignal).catch(() => setSignal(null)).finally(() => setSigLoading(false));
    } finally { setLoading(false); }
  }, [code, strategy, period, stopLoss, target, maxHold, useAtrStop, atrStopMult, volumeFilter, shrinkFilter, closeAbove, weeklyConf, maTrend, maPeriod, pullbackMin, pullbackMax, minScore, stabilizeBars, stabilizeDist, commission, slippage, cooldown, stockName]);

  const runOptimize = useCallback(async () => {
    setOptimizing(true); setOptResult(null);
    try {
      const res = await api.optimize({ code, strategy, period, target: optTarget, n_trials: 60 });
      setOptResult(res);
    } finally { setOptimizing(false); }
  }, [code, strategy, period, optTarget]);

  const applyOptParams = useCallback(() => {
    if (!optResult) return;
    const p = optResult.best_params;
    if (p.stop_loss_pct != null) setStopLoss(p.stop_loss_pct as number);
    if (p.target_pct != null) setTarget(p.target_pct as number);
    if (p.max_hold_bars != null) setMaxHold(p.max_hold_bars as number);
    if (p.use_atr_stop != null) setUseAtrStop(p.use_atr_stop as boolean);
    if (p.atr_stop_mult != null) setAtrStopMult(p.atr_stop_mult as number);
    if (p.volume_filter != null) setVolumeFilter(p.volume_filter as boolean);
    if (p.shrink_filter != null) setShrinkFilter(p.shrink_filter as boolean);
    if (p.ma_trend_filter != null) setMaTrend(p.ma_trend_filter as boolean);
    if (p.ma_trend_period != null) setMaPeriod(p.ma_trend_period as number);
    if (p.pullback_min_pct != null) setPullbackMin(p.pullback_min_pct as number);
    if (p.pullback_max_pct != null) setPullbackMax(p.pullback_max_pct as number);
    if (p.min_level_score != null) setMinScore(p.min_level_score as number);
    if (p.stabilize_bars != null) setStabilizeBars(p.stabilize_bars as number);
    if (p.stabilize_max_dist_pct != null) setStabilizeDist(p.stabilize_max_dist_pct as number);
    if (p.cooldown_bars != null) setCooldown(p.cooldown_bars as number);
  }, [optResult]);

  useEffect(() => { if (code) runBacktest(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const stats = result?.stats;
  const trades = result?.trades ?? [];
  const curve = result?.equity_curve ?? [];
  const candles: Candle[] = result?.candles ?? [];

  // Build markers for K-line chart
  const markers = trades.flatMap((t) => {
    const ms: { date: string; type: "buy" | "sell"; price: number; label?: string }[] = [];
    ms.push({ date: t.entry_date, type: "buy", price: t.entry_price, label: t.reason_entry });
    if (t.exit_date && !t.exit_date.startsWith("bar_"))
      ms.push({ date: t.exit_date, type: "sell", price: t.exit_price, label: exitLabel(t.reason_exit) });
    return ms;
  });

  // Horizontal lines: selected trade + support/resistance levels
  const hlines: { price: number; color: string; label: string; dash?: boolean }[] = [];
  if (selectedTrade !== null && trades[selectedTrade]) {
    const t = trades[selectedTrade];
    hlines.push({ price: t.entry_price, color: "#22c55e", label: "买入", dash: true });
    if (t.exit_price > 0) hlines.push({ price: t.exit_price, color: "#ef4444", label: "卖出", dash: true });
  }
  if (result) {
    for (const lv of result.levels_used) {
      hlines.push({
        price: lv.price,
        color: lv.kind === "support" ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.35)",
        label: lv.label, dash: true,
      });
    }
  }

  return (
    <div className="flex-1 grid" style={{ gridTemplateColumns: "280px 1fr 300px" }}>
      {/* ─── Left: Settings ─── */}
      <aside className="border-r border-ink-700 bg-ink-900 overflow-y-auto scrollbar flex flex-col">
        <div className="p-4 flex-1">
          <div ref={searchRef} className="relative mb-5">
            <div className="flex items-center gap-2 bg-ink-850 border border-ink-700 rounded-lg px-3 py-2">
              <i className="fas fa-search text-[11px] text-ink-500" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={handleSearchKeyDown}
                onFocus={() => { if (!query.trim() && code) { setQuery(code); setTimeout(() => searchRef.current?.querySelector("input")?.select(), 0); } if (query.trim()) setShowDropdown(true); }}
                placeholder="代码 / 名称" className="flex-1 bg-transparent text-[12px] focus:outline-none placeholder:text-ink-500" />
            </div>
            {code && !query && (
              <div className="mt-2 flex items-center gap-2">
                <span className="text-[14px] font-semibold text-white">{stockName || code}</span>
                <span className="text-[12px] text-ink-400 num">{code}</span>
              </div>
            )}
            {showDropdown && searchResults.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-ink-850 border border-ink-700 rounded-lg shadow-xl z-50 max-h-[240px] overflow-y-auto scrollbar">
                {searchResults.map((s) => (
                  <button key={s.code} onClick={() => pickStock(s.code, s.name)}
                    className="w-full text-left px-3 py-2 hover:bg-ink-800 flex justify-between items-baseline text-[12px] border-b border-ink-800/50 last:border-0">
                    <div><span className="text-ink-100">{s.name}</span><span className="text-ink-500 num ml-2">{s.code}</span></div>
                    <span className="text-[10px] text-ink-500">{s.industry}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <Sec label="策略">
            <div className="grid grid-cols-2 gap-1.5">
              {[
                { k: "breakout_pullback", l: "突破回踩", icon: "fa-arrow-trend-up" },
                { k: "bottom_stabilize", l: "下跌企稳", icon: "fa-shield-halved" },
              ].map((o) => (
                <button key={o.k} onClick={() => setStrategy(o.k)}
                  className={"flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[12px] transition " +
                    (strategy === o.k ? "bg-gold/15 text-gold border border-gold/30" : "text-ink-400 bg-ink-850 hover:bg-ink-800 border border-transparent")}>
                  <i className={`fas ${o.icon} text-[10px]`} />{o.l}
                </button>
              ))}
            </div>
          </Sec>

          <Sec label="回测区间">
            <div className="flex gap-1">
              {PERIODS.map((p) => (
                <button key={p.k} onClick={() => setPeriod(p.k)}
                  className={"flex-1 py-1.5 rounded-md text-[12px] num font-medium transition " +
                    (period === p.k ? "bg-ink-700 text-white" : "text-ink-500 hover:text-ink-300")}>
                  {p.l}
                </button>
              ))}
            </div>
          </Sec>

          <Sec label="风控参数">
            <div className="grid grid-cols-3 gap-1.5">
              <NumInput label="止损" value={stopLoss} onChange={setStopLoss} step={0.5} suffix="%" />
              <NumInput label="目标" value={target} onChange={setTarget} step={0.5} suffix="%" />
              <NumInput label="持仓" value={maxHold} onChange={setMaxHold} step={1} suffix="天" />
            </div>
          </Sec>

          <Sec label="信号过滤">
            <div className="space-y-0.5 text-[11px]">
              <Toggle label="放量突破（量比≥1.5）" value={volumeFilter} onChange={setVolumeFilter} />
              <Toggle label="缩量回踩" value={shrinkFilter} onChange={setShrinkFilter} />
              <Toggle label="收盘站稳支撑位" value={closeAbove} onChange={setCloseAbove} />
              <Toggle label="多周期共振" value={weeklyConf} onChange={setWeeklyConf} />
              <Toggle label={"MA" + maPeriod + " 趋势"} value={maTrend} onChange={setMaTrend} />
              <Toggle label="ATR 跟踪止损" value={useAtrStop} onChange={setUseAtrStop} />
            </div>
          </Sec>

          <button className="w-full text-[11px] text-ink-500 hover:text-ink-300 mb-3 flex items-center justify-center gap-1"
            onClick={() => setShowAdvanced(!showAdvanced)}>
            <i className={"fas fa-chevron-" + (showAdvanced ? "up" : "down") + " text-[9px]"} />
            {showAdvanced ? "收起" : "高级参数"}
          </button>
          {showAdvanced && (
            <div className="space-y-3 mb-4">
              <div className="grid grid-cols-2 gap-1.5">
                <NumInput label="最低评分" value={minScore} onChange={setMinScore} step={5} />
                <NumInput label="冷却期" value={cooldown} onChange={setCooldown} step={1} suffix="天" />
                {strategy === "breakout_pullback" ? (<>
                  <NumInput label="回踩下限" value={pullbackMin} onChange={setPullbackMin} step={0.1} suffix="%" />
                  <NumInput label="回踩上限" value={pullbackMax} onChange={setPullbackMax} step={0.5} suffix="%" />
                </>) : (<>
                  <NumInput label="企稳K线" value={stabilizeBars} onChange={setStabilizeBars} step={1} suffix="根" />
                  <NumInput label="最大距离" value={stabilizeDist} onChange={setStabilizeDist} step={0.5} suffix="%" />
                </>)}
                {maTrend && <NumInput label="MA周期" value={maPeriod} onChange={setMaPeriod} step={5} />}
                {useAtrStop && <NumInput label="ATR倍数" value={atrStopMult} onChange={setAtrStopMult} step={0.5} />}
                <NumInput label="手续费" value={commission} onChange={setCommission} step={0.01} suffix="%" />
                <NumInput label="滑点" value={slippage} onChange={setSlippage} step={0.01} suffix="%" />
              </div>
            </div>
          )}

          <button className="w-full bg-gold text-ink-950 font-semibold py-2.5 rounded-lg text-[13px] disabled:opacity-50 hover:brightness-110 transition"
            onClick={runBacktest} disabled={loading || !code}>
            {loading ? <><i className="fas fa-circle-notch fa-spin mr-1.5" />回测中...</> : <><i className="fas fa-play mr-1.5" />运行回测</>}
          </button>

          {/* Optimizer */}
          <div className="mt-4 pt-4 border-t border-ink-800">
            <Sec label={<><i className="fas fa-wand-magic-sparkles mr-1" />智能调参</>}>
              <div className="flex items-center gap-1 mb-2">
                {(["sharpe", "return", "calmar"] as const).map((t) => (
                  <button key={t} onClick={() => setOptTarget(t)}
                    className={"px-2 py-1 rounded text-[11px] " + (optTarget === t ? "bg-ink-700 text-white" : "text-ink-500 hover:text-ink-300")}>
                    {t === "sharpe" ? "Sharpe" : t === "return" ? "收益" : "Calmar"}
                  </button>
                ))}
              </div>
              <button className="w-full bg-ink-800 border border-ink-700 text-ink-300 hover:text-white py-2 rounded-lg text-[12px] disabled:opacity-50 transition"
                onClick={runOptimize} disabled={optimizing || !code}>
                {optimizing ? <><i className="fas fa-circle-notch fa-spin mr-1" />搜索中...</> : "自动搜索最优参数"}
              </button>
              {optResult && (
                <div className="mt-3 bg-ink-850 rounded-lg p-3 border border-ink-700 text-[11px]">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-ink-400">优化完成</span>
                    <span className="text-ink-500 num">{optResult.trials_count} trials</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 mb-2">
                    <div className="text-center"><div className="text-[9px] text-ink-500">默认</div><div className="num text-ink-400">{optResult.default_value.toFixed(2)}</div></div>
                    <div className="text-center"><div className="text-[9px] text-ink-500">最优</div>
                      <div className={"num font-semibold " + (optResult.best_value > optResult.default_value ? "text-cn-up" : "text-cn-dn")}>{optResult.best_value.toFixed(2)}</div>
                    </div>
                  </div>
                  <div className="flex gap-1.5">
                    <button className="flex-1 bg-gold/20 text-gold hover:bg-gold/30 py-1.5 rounded text-[11px] font-medium"
                      onClick={() => { applyOptParams(); setOptResult(null); }}><i className="fas fa-check mr-1" />应用</button>
                    <button className="flex-1 bg-ink-800 text-ink-300 hover:text-white py-1.5 rounded text-[11px]"
                      onClick={() => { applyOptParams(); setOptResult(null); setTimeout(runBacktest, 100); }}>应用并回测</button>
                  </div>
                </div>
              )}
            </Sec>
          </div>
        </div>
      </aside>

      {/* ─── Center: Charts ─── */}
      <section className="bg-ink-950 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800">
          <div className="flex items-center gap-4">
            <div>
              <span className="text-[16px] font-bold text-white">{stockName || code || "—"}</span>
              <span className="text-ink-500 num text-[13px] ml-2">{code}</span>
            </div>
            {stats && (
              <span className={"num text-[14px] font-semibold " + (stats.total_return >= 0 ? "text-cn-up" : "text-cn-dn")}>
                {stats.total_return >= 0 ? "+" : ""}{stats.total_return.toFixed(2)}%
              </span>
            )}
          </div>
          <div className="text-[11px] text-ink-500">
            {strategy === "breakout_pullback" ? "突破回踩" : "下跌企稳"} · {PERIODS.find((p) => p.k === period)?.l}
            {stats ? ` · ${stats.total_trades} 笔交易` : ""}
          </div>
        </div>

        {stats && (
          <div className="grid grid-cols-8 border-b border-ink-800">
            <StatCell label="累计收益" value={`${stats.total_return >= 0 ? "+" : ""}${stats.total_return.toFixed(2)}%`} color={stats.total_return >= 0 ? "text-cn-up" : "text-cn-dn"} />
            <StatCell label="胜率" value={`${(stats.win_rate * 100).toFixed(1)}%`} color={stats.win_rate >= 0.5 ? "text-cn-up" : "text-cn-dn"} />
            <StatCell label="盈亏比" value={stats.profit_factor.toFixed(2)} color="text-gold" />
            <StatCell label="最大回撤" value={`${stats.max_drawdown.toFixed(1)}%`} color="text-cn-dn" />
            <StatCell label="夏普" value={stats.sharpe.toFixed(2)} color={stats.sharpe >= 1 ? "text-cn-up" : stats.sharpe >= 0 ? "text-ink-200" : "text-cn-dn"} />
            <StatCell label="交易次数" value={String(stats.total_trades)} />
            <StatCell label="均盈" value={`+${stats.avg_win.toFixed(2)}%`} color="text-cn-up" />
            <StatCell label="均亏" value={`${stats.avg_loss.toFixed(2)}%`} color="text-cn-dn" />
          </div>
        )}

        {loading ? (
          <div className="flex-1 flex items-center justify-center text-ink-500">
            <i className="fas fa-circle-notch fa-spin mr-2 text-lg" />
            <span className="text-sm">正在计算回测...</span>
          </div>
        ) : candles.length > 0 || curve.length > 0 ? (
          <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
            {/* K-line chart */}
            {candles.length > 0 && (
              <div className="flex-[3] min-h-0 px-5 pt-2 pb-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-ink-500 flex items-center gap-1"><i className="fas fa-chart-bar text-[9px]" /> K线图</span>
                  {result && (
                    <div className="flex items-center gap-3 text-[10px] text-ink-500">
                      <span className="flex items-center gap-1"><span className="inline-block w-0 h-0 border-l-[3px] border-r-[3px] border-b-[6px] border-l-transparent border-r-transparent border-b-green-500" /> 买入</span>
                      <span className="flex items-center gap-1"><span className="inline-block w-0 h-0 border-l-[3px] border-r-[3px] border-t-[6px] border-l-transparent border-r-transparent border-t-red-500" /> 卖出</span>
                      {result.levels_used.length > 0 && (<>
                        <span className="flex items-center gap-1"><span className="w-3 h-[1px] bg-cn-up inline-block" /> 支撑</span>
                        <span className="flex items-center gap-1"><span className="w-3 h-[1px] bg-cn-dn inline-block" /> 压力</span>
                      </>)}
                    </div>
                  )}
                </div>
                <div className="h-[calc(100%-20px)]">
                  <TradeChart candles={candles} markers={markers} title="" hlines={hlines} />
                </div>
              </div>
            )}
            {/* Equity curve */}
            {curve.length > 0 && (
              <div className="flex-[2] min-h-0 px-5 pb-3 pt-1">
                <div className="h-full bg-ink-900 rounded-xl border border-ink-800 p-3">
                  <EquityChart points={curve} trades={trades} />
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-ink-600">
            <i className="fas fa-chart-candlestick text-4xl mb-3 text-ink-700" />
            <span className="text-sm">设置参数后点击「运行回测」</span>
          </div>
        )}
      </section>

      {/* ─── Right: Signal + Trades ─── */}
      <aside className="border-l border-ink-700 bg-ink-900 overflow-y-auto scrollbar flex flex-col">
        {(signal || sigLoading) && (
          <div className="p-4 border-b border-ink-800">
            <Sec label={<><i className="fas fa-crosshairs mr-1" />实时信号</>}>
              {sigLoading ? (
                <div className="text-[12px] text-ink-500 py-4 text-center"><i className="fas fa-circle-notch fa-spin mr-1" />分析中...</div>
              ) : signal && !signal.error ? <SignalCard signal={signal} /> : <div className="text-[12px] text-ink-500 py-2">无信号</div>}
            </Sec>
          </div>
        )}

        <div className="p-4 pb-2 border-b border-ink-800">
          <div className="flex items-center justify-between">
            <Sec label={<><i className="fas fa-list-ol mr-1" />交易明细</>} />
            <span className="text-[11px] text-ink-500 num">
              {trades.length} 笔{stats ? ` · 胜率 ${(stats.win_rate * 100).toFixed(0)}%` : ""}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto scrollbar">
          {trades.length === 0 && !loading && (
            <div className="text-[12px] text-ink-600 text-center py-10">
              <i className="fas fa-inbox text-2xl text-ink-700 block mb-2" />暂无交易记录
            </div>
          )}
          {trades.map((t, i) => (
            <TradeRow key={i} trade={t} index={i + 1} selected={selectedTrade === i}
              onClick={() => setSelectedTrade(selectedTrade === i ? null : i)} />
          ))}
        </div>

        {result && result.levels_used.length > 0 && (
          <div className="border-t border-ink-800 p-4">
            <Sec label={<><i className="fas fa-ruler-horizontal mr-1" />关键价位</>}>
              <div className="space-y-1">
                {result.levels_used.map((l, i) => (
                  <div key={i} className="flex items-center justify-between text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <span className={"w-1.5 h-1.5 rounded-full " + (l.kind === "support" ? "bg-cn-up" : "bg-cn-dn")} />
                      <span className={l.kind === "support" ? "text-cn-up" : "text-cn-dn"}>{l.label}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-ink-200 num">{l.price.toFixed(2)}</span>
                      <span className="text-ink-600 num text-[10px]">{l.score.toFixed(0)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </Sec>
          </div>
        )}
      </aside>
    </div>
  );
}

/* ================================================================ */
/*  Sub-components                                                    */
/* ================================================================ */

function exitLabel(reason: string): string {
  return { target: "止盈", stop: "止损", trail_stop: "跟踪止损", timeout: "超时", open: "持仓中" }[reason] || reason;
}

function Sec({ label, children }: { label: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="text-[10px] text-ink-500 uppercase tracking-wider mb-2">{label}</div>
      {children}
    </div>
  );
}

function StatCell({ label, value, color = "text-ink-200" }: { label: string; value: string; color?: string }) {
  return (
    <div className="px-3 py-2.5 text-center border-r border-ink-800 last:border-0">
      <div className="text-[9px] text-ink-500 uppercase">{label}</div>
      <div className={`num text-[14px] font-semibold mt-0.5 ${color}`}>{value}</div>
    </div>
  );
}

function TabBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: string; label: string }) {
  return (
    <button onClick={onClick}
      className={"flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] transition " +
        (active ? "bg-ink-800 text-white" : "text-ink-500 hover:text-ink-300")}>
      <i className={`fas ${icon} text-[10px]`} />{label}
    </button>
  );
}

function TradeRow({ trade: t, index, selected, onClick }: { trade: BacktestTrade; index: number; selected: boolean; onClick: () => void }) {
  const win = t.pnl_net > 0;
  const exitMap: Record<string, { l: string; c: string }> = {
    target: { l: "止盈", c: "bg-cn-up/20 text-cn-up" },
    stop: { l: "止损", c: "bg-cn-dn/20 text-cn-dn" },
    trail_stop: { l: "跟踪止损", c: "bg-cn-dn/20 text-cn-dn" },
    timeout: { l: "超时", c: "bg-ink-750 text-ink-400" },
    open: { l: "持仓中", c: "bg-gold/20 text-gold" },
  };
  const ex = exitMap[t.reason_exit] || { l: t.reason_exit, c: "bg-ink-750 text-ink-400" };

  return (
    <div onClick={onClick}
      className={"px-4 py-2.5 border-b border-ink-850/60 cursor-pointer transition " +
        (selected ? "bg-ink-800/80 border-l-2 border-l-gold" : "hover:bg-ink-850/50")}>
      <div className="flex justify-between items-center mb-1.5">
        <div className="flex items-center gap-2">
          <span className={"w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold " + (win ? "bg-cn-up/15 text-cn-up" : "bg-cn-dn/15 text-cn-dn")}>
            {index}
          </span>
          <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${ex.c}`}>{ex.l}</span>
        </div>
        <span className={"num text-[13px] font-semibold " + (win ? "text-cn-up" : "text-cn-dn")}>
          {win ? "+" : ""}{t.pnl_net.toFixed(2)}%
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 text-[11px]">
        <div className="flex items-center justify-between">
          <span className="text-ink-500">买</span>
          <span className="num text-cn-up">{t.entry_price}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-ink-500">卖</span>
          <span className="num text-cn-dn">{t.exit_price}</span>
        </div>
      </div>
      <div className="flex items-center justify-between mt-1 text-[10px] text-ink-500">
        <span>{fmtDate(t.entry_date)} → {fmtDate(t.exit_date)}</span>
        <span>{t.holding_bars}天</span>
      </div>
    </div>
  );
}

/* ── Equity Chart ── */
function EquityChart({ points, trades }: { points: { date: string; equity: number; benchmark: number }[]; trades: BacktestTrade[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const W = 800, H = 260;
  const ys = points.flatMap((p) => [p.equity - 100, p.benchmark]);
  const minY = Math.min(...ys, 0) - 1;
  const maxY = Math.max(...ys, 0) + 1;
  const xScale = (i: number) => (i / Math.max(1, points.length - 1)) * W;
  const yScale = (v: number) => H - ((v - minY) / (maxY - minY)) * H;

  const strategyPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(p.equity - 100).toFixed(1)}`).join(" ");
  const benchPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(p.benchmark).toFixed(1)}`).join(" ");

  const dateMap = new Map(points.map((p, i) => [p.date, i]));
  const entryM: { x: number; y: number }[] = [];
  const exitM: { x: number; y: number }[] = [];
  // Build trade event lookup: date → trade info
  const tradeEventMap = new Map<string, { type: "buy" | "sell"; trade: BacktestTrade; index: number }>();
  for (let ti = 0; ti < trades.length; ti++) {
    const t = trades[ti];
    const ei = dateMap.get(t.entry_date);
    const xi = dateMap.get(t.exit_date);
    if (ei !== undefined) entryM.push({ x: xScale(ei), y: yScale(points[ei].equity - 100) });
    if (xi !== undefined) exitM.push({ x: xScale(xi), y: yScale(points[xi].equity - 100) });
    if (!tradeEventMap.has(t.entry_date)) tradeEventMap.set(t.entry_date, { type: "buy", trade: t, index: ti + 1 });
    if (t.exit_date && !t.exit_date.startsWith("bar_") && !tradeEventMap.has(t.exit_date))
      tradeEventMap.set(t.exit_date, { type: "sell", trade: t, index: ti + 1 });
  }

  const step = Math.max(1, Math.floor(points.length / 6));
  const xLabels = points.filter((_, i) => i % step === 0 || i === points.length - 1);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const el = containerRef.current;
    if (!el || points.length === 0) return;
    const svg = el.querySelector("svg");
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const xRatio = (e.clientX - rect.left) / rect.width;
    const idx = Math.round(xRatio * (points.length - 1));
    setHoverIdx(Math.max(0, Math.min(points.length - 1, idx)));
  }, [points.length]);

  const hp = hoverIdx !== null ? points[hoverIdx] : null;
  const hTradeEvent = hp ? tradeEventMap.get(hp.date) : null;
  const hx = hoverIdx !== null ? xScale(hoverIdx) : 0;

  return (
    <div ref={containerRef} className="w-full h-full relative"
      onMouseMove={handleMouseMove} onMouseLeave={() => setHoverIdx(null)}>
      <div className="flex items-center gap-4 text-[10px] text-ink-500 mb-2">
        <span className="flex items-center gap-1"><span className="w-3 h-[2px] bg-gold inline-block rounded" /> 策略净值</span>
        <span className="flex items-center gap-1"><span className="w-3 h-[2px] bg-ink-500 inline-block rounded" /> 买入持有</span>
        {hp && (
          <span className="ml-auto flex items-center gap-3 text-ink-300">
            <span>{hp.date}</span>
            <span>策略 <span className={(hp.equity - 100) >= 0 ? "text-cn-up" : "text-cn-dn"}>
              {(hp.equity - 100) >= 0 ? "+" : ""}{(hp.equity - 100).toFixed(2)}%</span></span>
            <span>基准 <span className={hp.benchmark >= 0 ? "text-cn-up" : "text-cn-dn"}>
              {hp.benchmark >= 0 ? "+" : ""}{hp.benchmark.toFixed(2)}%</span></span>
            <span>超额 <span className={(hp.equity - 100 - hp.benchmark) >= 0 ? "text-cn-up" : "text-cn-dn"}>
              {(hp.equity - 100 - hp.benchmark) >= 0 ? "+" : ""}{(hp.equity - 100 - hp.benchmark).toFixed(2)}%</span></span>
            {hTradeEvent && (
              <span className={hTradeEvent.type === "buy" ? "text-cn-up" : "text-cn-dn"}>
                <i className={"fas " + (hTradeEvent.type === "buy" ? "fa-arrow-up" : "fa-arrow-down") + " text-[8px] mr-0.5"} />
                #{hTradeEvent.index} {hTradeEvent.type === "buy" ? "买入" : "卖出"} {hTradeEvent.type === "buy" ? hTradeEvent.trade.entry_price : hTradeEvent.trade.exit_price}
              </span>
            )}
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H + 24}`} className="w-full" style={{ height: "calc(100% - 20px)" }} preserveAspectRatio="none">
        <defs>
          <linearGradient id="bt-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#d4a857" stopOpacity="0.15" />
            <stop offset="100%" stopColor="#d4a857" stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={0} x2={W} y1={yScale(0)} y2={yScale(0)} stroke="#1e2633" strokeDasharray="3 4" />
        {[minY, 0, maxY].map((v) => (
          <text key={v} x={4} y={yScale(v) - 3} fill="#4a5568" fontSize="9" fontFamily="monospace">{v >= 0 ? "+" : ""}{v.toFixed(1)}%</text>
        ))}
        <path d={benchPath} fill="none" stroke="#3a4254" strokeWidth="1.2" />
        <path d={`${strategyPath} L ${W} ${H} L 0 ${H} Z`} fill="url(#bt-area)" />
        <path d={strategyPath} fill="none" stroke="#d4a857" strokeWidth="1.8" />
        {entryM.map((m, i) => <polygon key={`e${i}`} points={`${m.x},${m.y - 4} ${m.x - 4},${m.y + 4} ${m.x + 4},${m.y + 4}`} fill="#22c55e" opacity="0.8" />)}
        {exitM.map((m, i) => <polygon key={`x${i}`} points={`${m.x},${m.y + 4} ${m.x - 4},${m.y - 4} ${m.x + 4},${m.y - 4}`} fill="#ef4444" opacity="0.8" />)}
        {/* Crosshair + dots */}
        {hoverIdx !== null && hp && (<>
          <line x1={hx} x2={hx} y1={0} y2={H} stroke="#4a5568" strokeWidth="0.8" strokeDasharray="3 3" />
          <circle cx={hx} cy={yScale(hp.equity - 100)} r="3" fill="#d4a857" stroke="#0d1117" strokeWidth="1" />
          <circle cx={hx} cy={yScale(hp.benchmark)} r="3" fill="#4a5568" stroke="#0d1117" strokeWidth="1" />
        </>)}
        {xLabels.map((p, i) => {
          const idx = points.indexOf(p);
          return <text key={i} x={xScale(idx)} y={H + 16} fill="#4a5568" fontSize="9" fontFamily="monospace" textAnchor="middle">{fmtDate(p.date)}</text>;
        })}
      </svg>
    </div>
  );
}

/* ── Signal Card ── */
function SignalCard({ signal: s }: { signal: TradeSignal }) {
  const actionMap: Record<string, { label: string; bg: string; text: string; icon: string }> = {
    buy: { label: "买入", bg: "bg-cn-up/15", text: "text-cn-up", icon: "fa-arrow-trend-up" },
    near_signal: { label: "接近", bg: "bg-gold/15", text: "text-gold", icon: "fa-bell" },
    wait: { label: "观望", bg: "bg-ink-800", text: "text-ink-400", icon: "fa-hourglass-half" },
  };
  const a = actionMap[s.action] || actionMap.wait;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className={`${a.bg} ${a.text} px-2.5 py-1 rounded-md text-[12px] font-semibold flex items-center gap-1`}>
          <i className={`fas ${a.icon} text-[10px]`} />{a.label}
        </div>
        <div className="flex items-center gap-1">
          <div className="w-10 h-1.5 bg-ink-800 rounded-full overflow-hidden">
            <div className={"h-full rounded-full " + (s.confidence >= 60 ? "bg-cn-up" : s.confidence >= 30 ? "bg-gold" : "bg-ink-600")}
              style={{ width: `${s.confidence}%` }} />
          </div>
          <span className="num text-[11px] text-ink-300">{s.confidence}</span>
        </div>
      </div>
      <div className="text-[11px] text-ink-400 bg-ink-850 rounded-md px-2.5 py-1.5">{s.reason}</div>
      {s.action !== "wait" && (
        <div className="grid grid-cols-3 gap-1">
          <PriceBox label="入场" value={s.entry_price} color="text-white" />
          <PriceBox label="止损" value={s.stop_loss} color="text-cn-dn" sub={`-${s.risk_pct}%`} />
          <PriceBox label="目标" value={s.target_price} color="text-cn-up" sub={`+${s.reward_pct}%`} />
        </div>
      )}
      {s.action !== "wait" && (
        <div className="grid grid-cols-3 gap-1 text-[10px]">
          <div className="bg-ink-850 rounded px-2 py-1 text-center">
            <div className="text-ink-500">盈亏比</div>
            <div className={"num font-semibold " + (s.risk_reward >= 2 ? "text-cn-up" : "text-gold")}>1:{s.risk_reward.toFixed(1)}</div>
          </div>
          <div className="bg-ink-850 rounded px-2 py-1 text-center">
            <div className="text-ink-500">仓位</div>
            <div className="num text-ink-200">{s.suggested_position_pct}%</div>
          </div>
          <div className="bg-ink-850 rounded px-2 py-1 text-center">
            <div className="text-ink-500">趋势</div>
            <div className={s.trend === "up" ? "text-cn-up" : s.trend === "down" ? "text-cn-dn" : "text-ink-400"}>
              {s.trend === "up" ? "↑" : s.trend === "down" ? "↓" : "→"}
            </div>
          </div>
        </div>
      )}
      {s.factors.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {s.factors.map((f, i) => <span key={i} className="chip text-[9px]">{f}</span>)}
        </div>
      )}
    </div>
  );
}

function PriceBox({ label, value, color, sub }: { label: string; value: number; color: string; sub?: string }) {
  return (
    <div className="bg-ink-850 rounded px-2 py-1.5 text-center">
      <div className="text-[9px] text-ink-500">{label}</div>
      <div className={`num text-[13px] font-semibold ${color}`}>{value.toFixed(2)}</div>
      {sub && <div className="text-[8px] text-ink-500 num">{sub}</div>}
    </div>
  );
}

function NumInput({ label, value, onChange, step = 1, suffix }: { label: string; value: number; onChange: (v: number) => void; step?: number; suffix?: string }) {
  return (
    <label className="bg-ink-850 border border-ink-700 rounded-lg px-2.5 py-1.5 flex flex-col">
      <span className="text-[9px] text-ink-500">{label}</span>
      <div className="flex items-baseline gap-0.5">
        <input value={value} onChange={(e) => onChange(Number(e.target.value))} type="number" step={step}
          className="bg-transparent num text-[13px] text-ink-100 focus:outline-none w-full" />
        {suffix && <span className="text-[10px] text-ink-500">{suffix}</span>}
      </div>
    </label>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!value)}
      className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-md hover:bg-ink-850 text-ink-300">
      <span>{label}</span>
      <span className={"w-7 h-3.5 rounded-full relative transition " + (value ? "bg-gold/60" : "bg-ink-700")}>
        <span className={"absolute top-0.5 w-2.5 h-2.5 rounded-full bg-white transition " + (value ? "left-3.5" : "left-0.5")} />
      </span>
    </button>
  );
}

function fmtDate(d: string) {
  if (!d || d.startsWith("bar_")) return "—";
  const parts = d.split("-");
  if (parts.length >= 3) return `${parts[1]}-${parts[2].slice(0, 2)}`;
  return d.slice(0, 10);
}
