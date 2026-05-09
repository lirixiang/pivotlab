import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { BacktestResponse, BacktestTrade, OptimizeResult, TradeSignal } from "../types";

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

  // Resolve stock name from code (for initial load & direct code entry)
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

  // Allow direct code entry via Enter key
  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (searchResults.length > 0) {
        pickStock(searchResults[0].code, searchResults[0].name);
      } else if (query.trim().match(/^\d{6}$/)) {
        // Direct 6-digit code entry
        const c = query.trim();
        setCode(c);
        setStockName("");
        resolveStockName(c);
        setQuery("");
        setShowDropdown(false);
      }
    } else if (e.key === "Escape") {
      setShowDropdown(false);
      setQuery("");
    }
  };

  const runBacktest = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        code,
        strategy,
        period,
        stop_loss: stopLoss,
        target,
        max_hold_bars: maxHold,
        use_atr_stop: useAtrStop,
        atr_stop_mult: atrStopMult,
        volume_filter: volumeFilter,
        shrink_filter: shrinkFilter,
        close_above_support: closeAbove,
        weekly_confluence: weeklyConf,
        ma_trend_filter: maTrend,
        ma_trend_period: maPeriod,
        pullback_min_pct: pullbackMin,
        pullback_max_pct: pullbackMax,
        min_level_score: minScore,
        stabilize_bars: stabilizeBars,
        stabilize_max_dist_pct: stabilizeDist,
        commission_pct: commission,
        slippage_pct: slippage,
        cooldown_bars: cooldown,
      };
      const res = await api.backtest(params);
      setResult(res);
      if (!stockName) resolveStockName(code);
      // Fetch live signal with same params + backtest stats
      setSigLoading(true);
      api.signal({ ...params, backtest_stats: res.stats })
        .then(setSignal)
        .catch(() => setSignal(null))
        .finally(() => setSigLoading(false));
    } finally {
      setLoading(false);
    }
  }, [code, strategy, period, stopLoss, target, maxHold, useAtrStop, atrStopMult, volumeFilter, shrinkFilter, closeAbove, weeklyConf, maTrend, maPeriod, pullbackMin, pullbackMax, minScore, stabilizeBars, stabilizeDist, commission, slippage, cooldown, stockName]);

  const runOptimize = useCallback(async () => {
    setOptimizing(true);
    setOptResult(null);
    try {
      const res = await api.optimize({
        code, strategy, period, target: optTarget, n_trials: 60,
      });
      setOptResult(res);
    } finally {
      setOptimizing(false);
    }
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
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              onFocus={() => {
                if (!query.trim() && code) {
                  // Pre-fill with code so user can see what's selected, then type to search
                  setQuery(code);
                  // Select all text so typing replaces it
                  setTimeout(() => {
                    const el = wrapRef.current?.querySelector("input");
                    el?.select();
                  }, 0);
                }
                if (query.trim()) setShowDropdown(true);
              }}
              placeholder="输入代码 / 名称搜索"
              className="w-full bg-ink-850 border border-ink-700 rounded-md px-3 py-2 text-[12px] focus:outline-none focus:border-gold/60 placeholder:text-ink-500"
            />
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
          <div className="grid grid-cols-3 gap-2 text-[12px]">
            <NumInput label="止损 %" value={stopLoss} onChange={setStopLoss} step={0.5} />
            <NumInput label="目标 %" value={target} onChange={setTarget} step={0.5} />
            <NumInput label="最大持仓" value={maxHold} onChange={setMaxHold} step={1} suffix="天" />
          </div>
        </Block>

        <Block label="过滤">
          <div className="space-y-1.5 text-[12px] text-ink-300">
            <Toggle label="放量突破（量比 ≥1.5）" value={volumeFilter} onChange={setVolumeFilter} />
            <Toggle label="缩量回踩 ≤ 突破量 50%" value={shrinkFilter} onChange={setShrinkFilter} />
            <Toggle label="收盘价站稳支撑位" value={closeAbove} onChange={setCloseAbove} />
            <Toggle label="多周期共振（日+周）" value={weeklyConf} onChange={setWeeklyConf} />
            <Toggle label={"均线趋势过滤（MA" + maPeriod + "）"} value={maTrend} onChange={setMaTrend} />
            <Toggle label="ATR 跟踪止损" value={useAtrStop} onChange={setUseAtrStop} />
          </div>
        </Block>

        {/* Advanced toggle */}
        <button
          className="w-full text-[11px] text-ink-500 hover:text-ink-300 mb-3 flex items-center justify-center gap-1"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          <i className={"fas fa-chevron-" + (showAdvanced ? "up" : "down") + " text-[9px]"} />
          {showAdvanced ? "收起高级参数" : "展开高级参数"}
        </button>

        {showAdvanced && (
          <>
            <Block label="策略阈值">
              <div className="grid grid-cols-2 gap-2 text-[12px]">
                <NumInput label="最低评分" value={minScore} onChange={setMinScore} step={5} />
                <NumInput label="冷却期" value={cooldown} onChange={setCooldown} step={1} suffix="天" />
                {strategy === "breakout_pullback" ? (
                  <>
                    <NumInput label="回踩下限 %" value={pullbackMin} onChange={setPullbackMin} step={0.1} />
                    <NumInput label="回踩上限 %" value={pullbackMax} onChange={setPullbackMax} step={0.5} />
                  </>
                ) : (
                  <>
                    <NumInput label="企稳K线" value={stabilizeBars} onChange={setStabilizeBars} step={1} suffix="根" />
                    <NumInput label="最大距离 %" value={stabilizeDist} onChange={setStabilizeDist} step={0.5} />
                  </>
                )}
              </div>
            </Block>

            {maTrend && (
              <Block label="均线参数">
                <div className="grid grid-cols-2 gap-2 text-[12px]">
                  <NumInput label="MA 周期" value={maPeriod} onChange={setMaPeriod} step={5} />
                </div>
              </Block>
            )}

            {useAtrStop && (
              <Block label="ATR 止损">
                <div className="grid grid-cols-2 gap-2 text-[12px]">
                  <NumInput label="ATR 倍数" value={atrStopMult} onChange={setAtrStopMult} step={0.5} />
                </div>
              </Block>
            )}

            <Block label="交易成本">
              <div className="grid grid-cols-2 gap-2 text-[12px]">
                <NumInput label="手续费 %" value={commission} onChange={setCommission} step={0.01} />
                <NumInput label="滑点 %" value={slippage} onChange={setSlippage} step={0.01} />
              </div>
            </Block>
          </>
        )}

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

        {/* ── Optimizer section ── */}
        <div className="mt-4 pt-4 border-t border-ink-800">
          <div className="tag text-ink-500 mb-2">
            <i className="fas fa-sliders-h mr-1" /> 智能调参 (Optuna)
          </div>
          <div className="flex items-center gap-1.5 mb-2">
            {(["sharpe", "return", "calmar"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setOptTarget(t)}
                className={
                  "px-2 py-1 rounded text-[11px] " +
                  (optTarget === t ? "bg-ink-800 text-white ring-soft" : "text-ink-400 hover:text-white")
                }
              >
                {t === "sharpe" ? "Sharpe" : t === "return" ? "收益率" : "Calmar"}
              </button>
            ))}
          </div>
          <button
            className="w-full bg-ink-800 ring-soft text-ink-200 hover:text-white font-medium py-2 rounded-md text-[12px] disabled:opacity-50"
            onClick={runOptimize}
            disabled={optimizing || !code}
          >
            {optimizing ? (
              <><i className="fas fa-circle-notch fa-spin mr-1" /> 搜索最优参数...</>
            ) : (
              <><i className="fas fa-wand-magic-sparkles mr-1" /> 自动搜索最优参数</>
            )}
          </button>
          {optResult && (
            <div className="mt-3 bg-ink-850 rounded-md p-3 ring-soft text-[11px]">
              <div className="flex items-center justify-between mb-2">
                <span className="text-ink-400">优化结果</span>
                <span className="text-ink-500 num">{optResult.trials_count} 次试验</span>
              </div>
              <div className="grid grid-cols-2 gap-2 mb-2">
                <div className="text-center">
                  <div className="text-[9px] text-ink-500">默认参数</div>
                  <div className="num text-ink-300">{optResult.default_value.toFixed(2)}</div>
                </div>
                <div className="text-center">
                  <div className="text-[9px] text-ink-500">最优参数</div>
                  <div className={"num font-semibold " + (optResult.best_value > optResult.default_value ? "text-cn-up" : "text-cn-dn")}>
                    {optResult.best_value.toFixed(2)}
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-1 mb-2 text-[10px]">
                <div className="text-center">
                  <div className="text-ink-500">胜率</div>
                  <div className="num text-ink-200">{(optResult.best_stats.win_rate * 100).toFixed(0)}%</div>
                </div>
                <div className="text-center">
                  <div className="text-ink-500">收益</div>
                  <div className={"num " + (optResult.best_stats.total_return >= 0 ? "text-cn-up" : "text-cn-dn")}>
                    {optResult.best_stats.total_return.toFixed(1)}%
                  </div>
                </div>
                <div className="text-center">
                  <div className="text-ink-500">回撤</div>
                  <div className="num text-cn-dn">{optResult.best_stats.max_drawdown.toFixed(1)}%</div>
                </div>
              </div>
              <div className="flex gap-1.5">
                <button
                  className="flex-1 bg-gold/20 text-gold hover:bg-gold/30 py-1.5 rounded text-[11px] font-medium"
                  onClick={() => { applyOptParams(); setOptResult(null); }}
                >
                  <i className="fas fa-check mr-1" />应用参数
                </button>
                <button
                  className="flex-1 bg-ink-800 text-ink-300 hover:text-white py-1.5 rounded text-[11px]"
                  onClick={() => { applyOptParams(); setOptResult(null); setTimeout(runBacktest, 100); }}
                >
                  应用并回测
                </button>
              </div>
            </div>
          )}
        </div>
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
          <div className="grid grid-cols-4 gap-3 p-5 border-b border-ink-800">
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
            <div className="grid grid-cols-4 gap-3 mt-4">
              <Stat2 label="交易次数" value={String(stats.total_trades)} />
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
                label="夏普比率"
                value={String(stats.sharpe)}
                color={stats.sharpe >= 1 ? "text-cn-up" : stats.sharpe >= 0 ? "text-ink-100" : "text-cn-dn"}
              />
            </div>
          )}
        </div>
      </section>

      {/* ── Right sidebar: Signal + Trade list ── */}
      <aside className="border-l border-ink-700 bg-ink-900 overflow-y-auto scrollbar flex flex-col">
        {/* ── Signal Panel ── */}
        {(signal || sigLoading) && (
          <div className="p-4 border-b border-ink-800">
            <div className="tag text-ink-500 mb-2">
              <i className="fas fa-crosshairs mr-1" /> 实时交易信号
            </div>
            {sigLoading ? (
              <div className="text-[12px] text-ink-500 py-4 text-center">
                <i className="fas fa-circle-notch fa-spin mr-1" /> 分析中...
              </div>
            ) : signal && !signal.error ? (
              <SignalCard signal={signal} />
            ) : (
              <div className="text-[12px] text-ink-500 py-2">无法获取信号</div>
            )}
          </div>
        )}
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
  const win = t.pnl_net > 0;
  const exitLabel: Record<string, string> = {
    target: "止盈",
    stop: "止损",
    trail_stop: "跟踪止损",
    timeout: "超时",
    open: "持仓中",
  };
  return (
    <div className="px-4 py-2.5 border-b border-ink-850/60 row-hover">
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-[11px] text-ink-500">#{index}</span>
        <div className="text-right">
          <span className={
            "num text-[13px] font-medium " + (win ? "text-cn-up" : "text-cn-dn")
          }>
            {win ? "+" : ""}{t.pnl_net.toFixed(2)}%
          </span>
          {t.pnl_pct !== t.pnl_net && (
            <span className="text-[9px] text-ink-500 ml-1">
              (毛{t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct.toFixed(1)}%)
            </span>
          )}
        </div>
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
             t.reason_exit === "stop" || t.reason_exit === "trail_stop" ? "bg-cn-dn/20 text-cn-dn" :
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

/* ── Signal Card ── */
function SignalCard({ signal: s }: { signal: TradeSignal }) {
  const actionMap: Record<string, { label: string; bg: string; text: string; icon: string }> = {
    buy: { label: "建议买入", bg: "bg-cn-up/15", text: "text-cn-up", icon: "fa-arrow-trend-up" },
    near_signal: { label: "接近信号", bg: "bg-gold/15", text: "text-gold", icon: "fa-bell" },
    wait: { label: "等待观望", bg: "bg-ink-800", text: "text-ink-400", icon: "fa-hourglass-half" },
  };
  const a = actionMap[s.action] || actionMap.wait;
  const trendMap: Record<string, { l: string; c: string }> = {
    up: { l: "↑ 上升", c: "text-cn-up" },
    down: { l: "↓ 下降", c: "text-cn-dn" },
    neutral: { l: "→ 震荡", c: "text-ink-400" },
  };
  const t = trendMap[s.trend] || trendMap.neutral;

  return (
    <div className="space-y-2.5">
      {/* Action badge + confidence */}
      <div className="flex items-center justify-between">
        <div className={`${a.bg} ${a.text} px-3 py-1.5 rounded-md text-[13px] font-semibold flex items-center gap-1.5`}>
          <i className={`fas ${a.icon} text-[11px]`} />
          {a.label}
        </div>
        <div className="text-right">
          <div className="text-[10px] text-ink-500">置信度</div>
          <div className="flex items-center gap-1">
            <div className="w-12 h-1.5 bg-ink-800 rounded-full overflow-hidden">
              <div
                className={"h-full rounded-full " + (s.confidence >= 60 ? "bg-cn-up" : s.confidence >= 30 ? "bg-gold" : "bg-ink-600")}
                style={{ width: `${s.confidence}%` }}
              />
            </div>
            <span className="num text-[12px] text-ink-200">{s.confidence}</span>
          </div>
        </div>
      </div>

      {/* Reason */}
      <div className="text-[12px] text-ink-300 bg-ink-850 rounded-md px-3 py-2">
        {s.reason}
      </div>

      {/* Price grid */}
      {s.action !== "wait" && (
        <div className="grid grid-cols-3 gap-1.5">
          <PriceBox label="入场价" value={s.entry_price} color="text-white" />
          <PriceBox label="止损价" value={s.stop_loss} color="text-cn-dn" sub={`-${s.risk_pct}%`} />
          <PriceBox label="目标价" value={s.target_price} color="text-cn-up" sub={`+${s.reward_pct}%`} />
        </div>
      )}

      {/* Risk/Reward + Position */}
      {s.action !== "wait" && (
        <div className="grid grid-cols-3 gap-1.5">
          <div className="bg-ink-850 rounded-md px-2 py-1.5 text-center">
            <div className="text-[9px] text-ink-500">盈亏比</div>
            <div className={"num text-[13px] font-semibold " + (s.risk_reward >= 2 ? "text-cn-up" : s.risk_reward >= 1 ? "text-gold" : "text-cn-dn")}>
              1:{s.risk_reward.toFixed(1)}
            </div>
          </div>
          <div className="bg-ink-850 rounded-md px-2 py-1.5 text-center">
            <div className="text-[9px] text-ink-500">建议仓位</div>
            <div className="num text-[13px] text-ink-100">{s.suggested_position_pct}%</div>
          </div>
          <div className="bg-ink-850 rounded-md px-2 py-1.5 text-center">
            <div className="text-[9px] text-ink-500">趋势</div>
            <div className={`text-[12px] ${t.c}`}>{t.l}</div>
          </div>
        </div>
      )}

      {/* Context row */}
      <div className="flex items-center justify-between text-[10px] text-ink-500">
        <span>支撑 <span className="num text-ink-300">{s.nearest_support}</span></span>
        <span>现价 <span className="num text-white">{s.current_price}</span></span>
        <span>压力 <span className="num text-ink-300">{s.nearest_resistance}</span></span>
      </div>

      {/* ATR */}
      <div className="text-[10px] text-ink-500 flex items-center gap-2">
        <span>ATR <span className="num text-ink-400">{s.atr}</span></span>
        <span>·</span>
        <span>策略 <span className="text-ink-400">{s.strategy === "breakout_pullback" ? "突破回踩" : "下跌企稳"}</span></span>
      </div>

      {/* Factors */}
      {s.factors.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {s.factors.map((f, i) => (
            <span key={i} className="chip text-[10px]">{f}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function PriceBox({ label, value, color, sub }: { label: string; value: number; color: string; sub?: string }) {
  return (
    <div className="bg-ink-850 rounded-md px-2 py-1.5 text-center">
      <div className="text-[9px] text-ink-500">{label}</div>
      <div className={`num text-[14px] font-semibold ${color}`}>{value.toFixed(2)}</div>
      {sub && <div className="text-[9px] text-ink-500 num">{sub}</div>}
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

function NumInput({ label, value, onChange, step = 1, suffix }: {
  label: string; value: number; onChange: (v: number) => void; step?: number; suffix?: string;
}) {
  return (
    <label className="bg-ink-850 ring-soft rounded-md px-2 py-1.5 flex flex-col">
      <span className="text-[10px] text-ink-500">{label}</span>
      <div className="flex items-baseline gap-0.5">
        <input
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          type="number" step={step}
          className="bg-transparent num text-[13px] text-ink-100 focus:outline-none w-full"
        />
        {suffix && <span className="text-[10px] text-ink-500">{suffix}</span>}
      </div>
    </label>
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
