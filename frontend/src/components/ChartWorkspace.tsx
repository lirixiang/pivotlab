import { useEffect, useRef, useState } from "react";
import type { StockDetail } from "../types";
import { api, type QuantSystemSummary, type QuantBacktestResult, type QuantTrade, type QuantBacktestMetrics, type QuantTestResult, type QuantSideReport } from "../services/api";
import { ChartCanvas } from "./ChartCanvas";
import { FinancialHistoryPanel } from "./FinancialHistoryPanel";

type Props = {
  data: StockDetail | null;
  loading: boolean;
  period: string;
  onPeriodChange: (p: string) => void;
  refreshing?: boolean;
  onRefresh?: () => void;
  isWatched?: boolean;
  onToggleWatch?: () => void;
  minScore?: number;
  onAIAnalyze?: (prompt: string, imageData?: string) => void;
  autoTriggerAI?: boolean;
  onAutoTriggerConsumed?: () => void;
  strategyId?: number;
  onStrategyConsumed?: () => void;
};

const PERIODS = ["日线", "周线", "月线", "季线"];

function fmtAmt(v: number) {
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)}亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
  return v.toFixed(0);
}

function fmtPct(v: number) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function statusColor(s: string) {
  if (s === "healthy") return "text-emerald-400";
  if (s === "risk") return "text-red-400";
  if (s === "weak") return "text-amber-400";
  return "text-ink-400";
}
function statusLabel(s: string) {
  if (s === "healthy") return "偏强";
  if (s === "risk") return "风险";
  if (s === "weak") return "偏弱";
  if (s === "neutral") return "中性";
  return "待同步";
}

export function ChartWorkspace({ data, loading, period, onPeriodChange, refreshing, onRefresh, isWatched, onToggleWatch, minScore = 80, onAIAnalyze, autoTriggerAI, onAutoTriggerConsumed, strategyId, onStrategyConsumed }: Props) {
  const chartAreaRef = useRef<HTMLDivElement>(null);
  const q = data?.quote;
  const up = (q?.change_pct ?? 0) >= 0;
  const f = q?.fundamentals;
  const ac = q?.analyst_consensus;
  const [showMA, setShowMA] = useState(true);
  const [showRes, setShowRes] = useState(true);
  const [showSup, setShowSup] = useState(true);
  const [showVP, setShowVP] = useState(false);

  // ── Strategy analysis state (backtest + signal) ──
  const [saSystems, setSaSystems] = useState<QuantSystemSummary[]>([]);
  const [saPickerOpen, setSaPickerOpen] = useState(false);
  const [saRunning, setSaRunning] = useState(false);
  const [saSystemName, setSaSystemName] = useState("");
  const [btResult, setBtResult] = useState<{ trades: QuantTrade[]; metrics: QuantBacktestMetrics; systemName: string } | null>(null);
  const [btError, setBtError] = useState("");
  const [sigResult, setSigResult] = useState<{ test: QuantTestResult; systemName: string } | null>(null);
  const [sigError, setSigError] = useState("");

  // load systems list when picker opens
  useEffect(() => {
    if (!saPickerOpen) return;
    api.quantList().then(setSaSystems).catch(() => {});
  }, [saPickerOpen]);

  const runAnalysis = async (systemId: number, systemName: string) => {
    const code = q?.code;
    if (!code) return;
    setSaPickerOpen(false);
    setSaRunning(true);
    setSaSystemName(systemName);
    setBtError("");
    setBtResult(null);
    setSigError("");
    setSigResult(null);

    // Update URL with strategy param
    const newUrl = `/stock/${code}?strategy=${systemId}`;
    if (window.location.pathname + window.location.search !== newUrl) {
      window.history.replaceState(null, "", newUrl);
    }

    // Run backtest + signal in parallel
    const btPromise = data?.candles?.length
      ? (async () => {
          try {
            const dates = data.candles.map((c) => c.date).sort();
            const res = await api.quantBacktestStock(systemId, { code, start_date: dates[0], end_date: dates[dates.length - 1] });
            if (res.error) setBtError(res.error);
            else setBtResult({ trades: res.trades, metrics: res.metrics, systemName });
          } catch (e: any) {
            setBtError(e?.message || "回测失败");
          }
        })()
      : Promise.resolve();

    const sigPromise = (async () => {
      try {
        const test = await api.quantTest(systemId, { code });
        setSigResult({ test, systemName });
      } catch (e: any) {
        setSigError(e?.message || "信号评估失败");
      }
    })();

    await Promise.all([btPromise, sigPromise]);
    setSaRunning(false);
  };

  const clearAnalysis = () => {
    setBtResult(null);
    setBtError("");
    setSigResult(null);
    setSigError("");
    setSaSystemName("");
    // Remove strategy param from URL
    const code = q?.code;
    if (code) {
      const cleanUrl = `/stock/${code}`;
      if (window.location.search) {
        window.history.replaceState(null, "", cleanUrl);
      }
    }
  };

  // Auto-trigger strategy analysis when navigating from SystemPage with strategyId
  const strategyFiredRef = useRef<string | null>(null);
  const currentCode = data?.quote?.code;
  useEffect(() => {
    if (!strategyId || !currentCode || loading) return;
    const key = `${currentCode}_${strategyId}`;
    if (strategyFiredRef.current === key) return;
    strategyFiredRef.current = key;
    // Find system name from list, then run
    api.quantList().then((systems) => {
      const sys = systems.find((s) => s.id === strategyId);
      if (sys) runAnalysis(sys.id, sys.name);
    }).catch(() => {});
  }, [strategyId, currentCode, loading]);

  // Auto-fire AI analysis when triggered from outside (e.g. from Screener page)
  const firedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!autoTriggerAI || !onAIAnalyze || !data) return;
    const code = data.quote?.code;
    if (!code || firedRef.current === code) return;
    // Wait one tick for canvas to be drawn
    const t = setTimeout(() => {
      const canvas = chartAreaRef.current?.querySelector("canvas");
      const imageData = canvas?.toDataURL("image/png");
      firedRef.current = code;
      onAIAnalyze("", imageData);
      onAutoTriggerConsumed?.();
    }, 350);
    return () => clearTimeout(t);
  }, [autoTriggerAI, data, onAIAnalyze, onAutoTriggerConsumed]);
  return (
    <section className="bg-ink-950 flex flex-col">
      {/* ─── Header row 1: stock info + price + change ─── */}
      <div className="flex items-center px-5 py-2.5 border-b border-ink-800 grad-head gap-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold text-white tracking-wide">
            {q?.name ?? "—"}
          </h1>
          <span className="text-ink-500 num text-sm">
            {q?.code ?? ""}
            {q?.code?.startsWith("6") ? ".SH" : q?.code ? ".SZ" : ""}
          </span>
          {q?.industry && <span className="chip chip-up">{q.industry}</span>}
          {q?.market && <span className="chip">{q.market}</span>}
          {q && (
            <button
              className={
                "ml-2 px-2.5 py-1 rounded-md text-[12px] font-medium flex items-center gap-1 transition " +
                (isWatched
                  ? "bg-gold/15 text-gold ring-1 ring-gold/30"
                  : "bg-ink-800 text-ink-400 hover:text-gold hover:bg-gold/10 ring-soft")
              }
              onClick={onToggleWatch}
              title={isWatched ? "移除自选" : "加入自选"}
            >
              <i className={"text-[11px] " + (isWatched ? "fas fa-star" : "far fa-star")} />
              {isWatched ? "已自选" : "加自选"}
            </button>
          )}
        </div>

        <div className="flex items-baseline gap-3 ml-4">
          <span className={"num text-2xl font-semibold " + (up ? "text-cn-up" : "text-cn-dn")}>
            {q ? q.price.toFixed(2) : "—"}
          </span>
          {q && (
            <span className={"num text-sm font-medium " + (up ? "text-cn-up" : "text-cn-dn")}>
              {up ? "+" : ""}
              {q.change.toFixed(2)} ({up ? "+" : ""}
              {q.change_pct.toFixed(2)}%)
            </span>
          )}
          {q && (
            <span className="text-[11px] text-ink-500">
              成交 {fmtAmt(q.amount)} · 量比 {q.volume_ratio.toFixed(2)} · 换手{" "}
              {(q.turnover_rate || q.turnover || 0).toFixed(2)}%
            </span>
          )}
        </div>
      </div>

      {/* ─── Header row 2: toolbar ─── */}
      <div className="flex items-center px-5 py-1.5 border-b border-ink-800 bg-ink-900/60 gap-3">
        <div className="seg">
          {PERIODS.map((p) => (
            <button key={p} className={p === period ? "on" : ""} onClick={() => onPeriodChange(p)}>
              {p}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <button className={"chip flex items-center gap-1.5" + (showRes ? " chip-on" : "")} onClick={() => setShowRes(!showRes)}>
            <span className={"dot " + (showRes ? "bg-gold" : "bg-ink-500")} />
            压力位
          </button>
          <button className={"chip flex items-center gap-1.5" + (showSup ? " chip-on" : "")} onClick={() => setShowSup(!showSup)}>
            <span className={"dot " + (showSup ? "bg-sky2" : "bg-ink-500")} />
            支撑位
          </button>
          <button className={"chip flex items-center gap-1.5" + (showMA ? " chip-on" : "")} onClick={() => setShowMA(!showMA)}>
            <span className={"dot " + (showMA ? "bg-purple-400" : "bg-ink-500")} />
            均线
          </button>
          <button className={"chip flex items-center gap-1.5" + (showVP ? " chip-on" : "")} onClick={() => setShowVP(!showVP)}>
            <span className={"dot " + (showVP ? "bg-amber-400" : "bg-ink-500")} />
            筹码
          </button>
        </div>

        <div className="flex-1" />

        {/* Strategy analysis trigger (backtest + signal combined) */}
        {q && (
          <div className="relative">
            <button
              className={"px-3 py-1.5 rounded-md ring-1 text-[12px] flex items-center gap-1.5 transition " +
                ((btResult || sigResult)
                  ? "bg-gold/15 text-gold ring-gold/30 hover:bg-gold/25"
                  : "bg-ink-800 text-ink-300 ring-ink-700 hover:text-white hover:bg-ink-750") +
                (saRunning ? " opacity-60" : "")}
              onClick={() => (btResult || sigResult) ? clearAnalysis() : setSaPickerOpen(!saPickerOpen)}
              disabled={saRunning}
              title={(btResult || sigResult) ? "清除策略分析结果" : "选择策略系统进行分析"}
            >
              {saRunning ? (
                <i className="fas fa-circle-notch fa-spin text-[10px]" />
              ) : (
                <i className="fas fa-flask text-[11px]" />
              )}
              {saRunning ? "分析中..." : (btResult || sigResult) ? `策略: ${saSystemName}` : "策略分析"}
            </button>
            {saPickerOpen && (
              <div className="absolute top-full right-0 mt-1 z-50 w-[220px] bg-ink-900 border border-ink-700 rounded-lg shadow-xl overflow-hidden">
                <div className="px-3 py-2 text-[11px] text-ink-400 border-b border-ink-800">选择策略系统（回测+信号）</div>
                {saSystems.length === 0 ? (
                  <div className="px-3 py-4 text-[11px] text-ink-500 text-center">
                    <i className="fas fa-circle-notch fa-spin mr-1" /> 加载中...
                  </div>
                ) : (
                  <div className="max-h-[200px] overflow-y-auto">
                    {saSystems.map((s) => (
                      <button
                        key={s.id}
                        className="w-full text-left px-3 py-2 text-[12px] text-ink-200 hover:bg-ink-800 hover:text-white transition flex items-center justify-between"
                        onClick={() => runAnalysis(s.id, s.name)}
                      >
                        <span className="truncate">{s.name}</span>
                        <span className="text-[10px] text-ink-500 ml-2 flex-shrink-0">{s.status === "active" ? "运行中" : s.status}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {onAIAnalyze && (
          <button
            className="px-3 py-1.5 rounded-md bg-amber-500/10 ring-1 ring-amber-500/20 text-[12px] text-amber-400 hover:bg-amber-500/20 hover:text-amber-300 flex items-center gap-1.5 transition"
            onClick={() => {
              // Capture K-line canvas as base64
              const canvas = chartAreaRef.current?.querySelector("canvas");
              const imageData = canvas?.toDataURL("image/png");
              onAIAnalyze("", imageData);
            }}
            title="用 AI 分析当前股票（含K线截图）"
          >
            <i className="fas fa-robot text-[11px]" />
            AI 分析
          </button>
        )}

        <button
          className="px-3 py-1.5 rounded-md bg-ink-800 ring-soft text-[12px] text-ink-200 hover:text-white flex items-center gap-1.5 disabled:opacity-50"
          onClick={onRefresh}
          disabled={refreshing || loading}
          title="从数据源刷新K线数据"
        >
          <i className={"fas fa-arrows-rotate text-[11px]" + (refreshing ? " fa-spin" : "")} />
          {refreshing ? "刷新中" : "刷新数据"}
        </button>
      </div>

      {/* ─── Main content: chart + side panel ─── */}
      <div className="flex flex-1 min-h-0">
        {/* Chart + backtest result area */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Chart area */}
          <div ref={chartAreaRef} className="relative px-5 pt-4 pb-2">
            {loading || !data ? (
              <div className="h-[560px] flex items-center justify-center text-ink-500 text-sm">
                <i className="fas fa-circle-notch fa-spin mr-2" /> 正在加载行情与画线...
              </div>
            ) : (
              <ChartCanvas candles={data.candles} levels={data.levels} consensus={q?.analyst_consensus} showMA={showMA} showResistance={showRes} showSupport={showSup} showVP={showVP} minScore={minScore} code={q?.code} name={q?.name} trades={btResult?.trades} />
            )}
          </div>

          {/* ─── Backtest result panel ─── */}
          {(btResult || btError) && (
            <div className="border-t border-ink-800 bg-ink-900/80 px-5 py-3 max-h-[220px] overflow-y-auto flex-shrink-0">
              {btError ? (
                <div className="text-red-400 text-[12px] flex items-center gap-2">
                  <i className="fas fa-exclamation-triangle" /> 回测失败: {btError}
                  <button className="ml-2 text-ink-500 hover:text-ink-300" onClick={clearAnalysis}><i className="fas fa-times" /></button>
                </div>
              ) : btResult && (
                <div className="space-y-2">
                  {/* Metrics row */}
                  <div className="flex items-center gap-4 text-[12px]">
                    <span className="text-ink-400 font-medium">
                      <i className="fas fa-flask text-cyan-400 mr-1 text-[10px]" />
                      {btResult.systemName}
                    </span>
                    <span className={btResult.metrics.total_return_pct >= 0 ? "text-cn-up num font-semibold" : "text-cn-dn num font-semibold"}>
                      {btResult.metrics.total_return_pct >= 0 ? "+" : ""}{btResult.metrics.total_return_pct.toFixed(2)}%
                    </span>
                    <span className="text-ink-400">胜率 <span className="text-white num">{btResult.metrics.win_rate_pct.toFixed(0)}%</span></span>
                    <span className="text-ink-400">最大回撤 <span className="text-red-400 num">-{btResult.metrics.max_drawdown_pct.toFixed(1)}%</span></span>
                    <span className="text-ink-400">交易 <span className="text-white num">{btResult.metrics.trade_count}</span> 笔</span>
                    <span className="text-ink-400">盈亏比 <span className="text-white num">{btResult.metrics.profit_factor.toFixed(2)}</span></span>
                    <div className="flex-1" />
                    <button className="text-ink-500 hover:text-ink-300 text-[11px]" onClick={clearAnalysis} title="清除分析">
                      <i className="fas fa-times" />
                    </button>
                  </div>
                  {/* Trades list */}
                  {btResult.trades.length > 0 && (
                    <div className="overflow-x-auto">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="text-ink-500 border-b border-ink-800">
                            <th className="text-left py-1 pr-3">日期</th>
                            <th className="text-left py-1 pr-3">方向</th>
                            <th className="text-right py-1 pr-3">价格</th>
                            <th className="text-right py-1 pr-3">数量</th>
                            <th className="text-right py-1 pr-3">盈亏</th>
                            <th className="text-right py-1 pr-3">盈亏%</th>
                            <th className="text-left py-1">原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {btResult.trades.map((t, i) => (
                            <tr key={i} className="border-b border-ink-800/50 hover:bg-ink-800/30">
                              <td className="py-1 pr-3 num text-ink-300">{t.date.slice(5)}</td>
                              <td className={"py-1 pr-3 font-medium " + (t.side === "open" ? "text-cn-up" : "text-cn-dn")}>
                                {t.side === "open" ? "▲ 买入" : "▼ 卖出"}
                              </td>
                              <td className="py-1 pr-3 num text-right text-ink-200">{t.price.toFixed(2)}</td>
                              <td className="py-1 pr-3 num text-right text-ink-300">{t.qty}</td>
                              <td className={"py-1 pr-3 num text-right " + (t.side === "close" ? (t.pnl && t.pnl > 0 ? "text-cn-up" : "text-cn-dn") : "text-ink-600")}>
                                {t.side === "close" && t.pnl != null ? (t.pnl > 0 ? "+" : "") + t.pnl.toFixed(0) : "—"}
                              </td>
                              <td className={"py-1 pr-3 num text-right " + (t.side === "close" ? (t.pnl_pct && t.pnl_pct > 0 ? "text-cn-up" : "text-cn-dn") : "text-ink-600")}>
                                {t.side === "close" && t.pnl_pct != null ? (t.pnl_pct > 0 ? "+" : "") + t.pnl_pct.toFixed(2) + "%" : "—"}
                              </td>
                              <td className="py-1 text-ink-500 truncate max-w-[160px]">{t.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ─── Side info panel (like SR KLineAnalysisPanel) ─── */}
        {q && (
          <aside className="w-[280px] flex-shrink-0 border-l border-ink-800 bg-ink-900/50 overflow-y-auto scrollbar px-4 py-4 text-[12px] space-y-3">
            {/* ── Signal evaluation (top of sidebar) ── */}
            {(sigResult || sigError) && (
              <>
                <div className="rounded-lg border border-ink-700 bg-ink-900/80 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-ink-300 font-medium text-[11px]">
                      <i className="fas fa-bolt text-gold mr-1 text-[10px]" />
                      信号评估
                      <span className="text-ink-600 ml-1">{sigResult?.systemName}</span>
                    </span>
                    <button className="text-ink-600 hover:text-ink-300 text-[10px]" onClick={clearAnalysis} title="清除">
                      <i className="fas fa-xmark" />
                    </button>
                  </div>
                  {sigError && <div className="text-red-400 text-[11px]">{sigError}</div>}
                  {sigResult && (
                    <div className="space-y-2">
                      <SignalSidePanel label="买入" side={sigResult.test.buy} />
                      <SignalSidePanel label="卖出" side={sigResult.test.sell} />
                      {sigResult.test.date && (
                        <div className="text-[10px] text-ink-600 text-right">评估日期: {sigResult.test.date}</div>
                      )}
                    </div>
                  )}
                </div>
                <div className="border-t border-ink-800" />
              </>
            )}

            {/* Stock headline */}
            <div>
              <div className="text-white font-semibold text-[14px]">{q.name}</div>
              <div className="text-ink-500 text-[11px] mt-0.5">
                {q.code} · {q.industry || "行业待同步"} · {q.market || ""}
              </div>
            </div>

            {/* Concepts */}
            <div className="flex flex-wrap gap-1.5">
              {q.concept_details && q.concept_details.length > 0
                ? q.concept_details.map((cd) => (
                    <span key={cd.concept} className={`chip text-[10px] ${
                      cd.heat_tone === 'concept-hot' ? 'chip-up' :
                      cd.heat_tone === 'concept-neutral' ? 'chip-neutral' :
                      'chip-watch'
                    }`}>
                      {cd.concept}{cd.heat_label && cd.heat_level !== 'observe' ? ` ${cd.heat_label}` : ''}
                    </span>
                  ))
                : q.concepts && q.concepts.length > 0
                  ? q.concepts.map((c) => (
                      <span key={c} className="chip chip-up text-[10px]">{c}</span>
                    ))
                  : <span className="chip text-[10px] text-ink-500">概念待同步</span>
              }
            </div>

            {/* Fundamentals tags */}
            <div className="flex flex-wrap gap-1.5">
              {f ? (
                <>
                  <span className={`chip text-[10px] ${statusColor(f.fundamental_status)}`}>
                    基本面{statusLabel(f.fundamental_status)}
                  </span>
                  {f.pe_ratio_ttm > 0 && (
                    <span className="chip text-[10px]">PE {f.pe_ratio_ttm.toFixed(1)}</span>
                  )}
                  {f.roe !== 0 && (
                    <span className="chip text-[10px]">ROE {f.roe.toFixed(1)}%</span>
                  )}
                </>
              ) : (
                <span className="chip text-[10px] text-ink-500">基本面待同步</span>
              )}
            </div>

            {f?.fundamental_summary && (
              <div className="text-ink-400 text-[11px] leading-relaxed">{f.fundamental_summary}</div>
            )}

            <div className="border-t border-ink-800" />

            {/* Price section */}
            <div>
              <div className={"num text-xl font-bold " + (up ? "text-cn-up" : "text-cn-dn")}>
                {q.price.toFixed(2)}
              </div>
              <div className={"num text-[11px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                {up ? "+" : ""}{q.change.toFixed(2)} ({fmtPct(q.change_pct)})
              </div>
            </div>

            <div className="border-t border-ink-800" />

            {/* Quote detail rows */}
            <div className="grid grid-cols-2 gap-y-1.5 gap-x-3 text-[11px]">
              <Row label="今开" value={q.open > 0 ? q.open.toFixed(2) : "—"} />
              <Row label="昨收" value={q.prev_close > 0 ? q.prev_close.toFixed(2) : "—"} />
              <Row label="最高" value={q.high > 0 ? q.high.toFixed(2) : "—"} up />
              <Row label="最低" value={q.low > 0 ? q.low.toFixed(2) : "—"} dn />
              <Row label="成交量" value={fmtAmt(q.volume)} />
              <Row label="成交额" value={fmtAmt(q.amount)} />
              <Row label="换手率" value={`${(q.turnover_rate || q.turnover || 0).toFixed(2)}%`} />
              <Row label="量比" value={q.volume_ratio.toFixed(2)} />
              <Row label="市盈率" value={q.pe_ratio > 0 ? q.pe_ratio.toFixed(1) : "—"} />
              <Row label="行业PE" value={q.industry_pe ? `${q.industry_pe.avg_pe}（${q.industry_pe.industry}）` : "—"} />
              <Row label="市值" value={q.market_cap > 0 ? fmtAmt(q.market_cap) : "—"} />
            </div>

            {/* Financials detail */}
            {f && (
              <>
                <div className="border-t border-ink-800" />
                <div className="text-ink-300 font-medium text-[11px]">
                  <i className="fas fa-chart-pie mr-1 text-[10px]" />
                  财务指标 <span className="text-ink-600">{f.report_period}</span>
                </div>
                <div className="grid grid-cols-2 gap-y-1.5 gap-x-3 text-[11px]">
                  <Row label="EPS(TTM)" value={f.eps_ttm.toFixed(2)} />
                  <Row label="ROE" value={`${f.roe.toFixed(1)}%`} />
                  <Row label="营收增长" value={fmtPct(f.revenue_yoy)} />
                  <Row label="利润增长" value={fmtPct(f.net_profit_yoy)} />
                  <Row label="营业收入" value={fmtAmt(f.total_revenue)} />
                  <Row label="净利润" value={fmtAmt(f.net_profit)} />
                </div>
              </>
            )}

            {/* Historical financials chart */}
            {q && <FinancialHistoryPanel code={q.code} />}

            {/* Analyst consensus */}
            {ac && ac.consensus_target && (
              <>
                <div className="border-t border-ink-800" />
                <div className="text-ink-300 font-medium text-[11px]">
                  <i className="fas fa-bullseye mr-1 text-[10px] text-purple-400" />
                  机构一致预期
                  <span className="text-ink-600 ml-1">{ac.analyst_count}家</span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="num text-purple-400 text-lg font-bold">{ac.consensus_target.toFixed(2)}</span>
                  <span className="text-ink-500 text-[10px]">一致目标价</span>
                  {q && ac.consensus_target > 0 && (
                    <span className={"num text-[10px] font-medium " + (ac.consensus_target > q.price ? "text-cn-up" : "text-cn-dn")}>
                      {ac.consensus_target > q.price ? "+" : ""}{((ac.consensus_target - q.price) / q.price * 100).toFixed(1)}%
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-y-1.5 gap-x-3 text-[11px]">
                  <Row label="目标高" value={ac.target_high != null ? ac.target_high.toFixed(2) : "—"} />
                  <Row label="目标低" value={ac.target_low != null ? ac.target_low.toFixed(2) : "—"} />
                  <Row label="买入" value={String(ac.buy_count)} />
                  <Row label="增持" value={String(ac.overweight_count)} />
                  <Row label="中性" value={String(ac.neutral_count)} />
                  <Row label="减持/卖出" value={String(ac.underweight_count + ac.sell_count)} />
                  {ac.eps_current_year != null && <Row label="今年EPS" value={ac.eps_current_year.toFixed(2)} />}
                  {ac.eps_next_year != null && <Row label="明年EPS" value={ac.eps_next_year.toFixed(2)} />}
                </div>
              </>
            )}

            {/* SR Levels summary */}
            {data && data.levels.length > 0 && (
              <>
                <div className="border-t border-ink-800" />
                <div className="text-ink-300 font-medium text-[11px]">
                  <i className="fas fa-chart-line mr-1 text-[10px]" />
                  关键价位
                </div>
                <div className="space-y-1">
                  {data.levels.map((lv) => (
                    <div key={lv.label} className="group">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className={lv.kind === "resistance" ? "text-amber-400" : "text-sky-400"}>
                          {lv.label}
                        </span>
                        <span className="num text-white">{lv.price.toFixed(2)}</span>
                        <span className="text-ink-500">{lv.distance_pct > 0 ? "+" : ""}{lv.distance_pct.toFixed(1)}%</span>
                        {(lv.score ?? 0) > 0 && (
                          <span className={
                            "num text-[10px] font-semibold " +
                            ((lv.score ?? 0) >= 70 ? "text-gold" : (lv.score ?? 0) >= 45 ? "text-ink-200" : "text-ink-500")
                          }>
                            {(lv.score ?? 0).toFixed(0)}分
                          </span>
                        )}
                      </div>
                      {lv.note && (
                        <div className="text-[10px] text-ink-500 mt-0.5 pl-1">{lv.note}</div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}

function Row({ label, value, up, dn }: { label: string; value: string; up?: boolean; dn?: boolean }) {
  return (
    <>
      <span className="text-ink-500">{label}</span>
      <span className={"num text-right " + (up ? "text-cn-up" : dn ? "text-cn-dn" : "text-ink-200")}>
        {value}
      </span>
    </>
  );
}

function SignalSidePanel({ label, side }: { label: string; side: QuantSideReport }) {
  if (side.rules.length === 0) return null;
  const triggered = side.triggered;
  const isHybrid = side.combine === "all_of+optional";
  const coreCount = side.core_count ?? side.rules.length;
  const coreRules = side.rules.slice(0, coreCount);
  const optRules = side.rules.slice(coreCount);
  const optHit = optRules.filter((r) => r.passed).length;
  return (
    <div className={"rounded border px-2.5 py-2 text-[11px] " + (triggered ? "border-emerald-700 bg-emerald-900/15" : "border-ink-800 bg-ink-900/30")}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className={"font-semibold " + (triggered ? "text-emerald-300" : "text-ink-400")}>
          {triggered ? "✓" : "✗"} {label}信号
        </span>
        <span className="text-ink-600 text-[10px]">
          {isHybrid ? "核心+可选" : side.combine === "all_of" ? "全部满足" : side.combine === "any_of" ? "任一满足" : ""}
        </span>
      </div>
      {isHybrid ? (
        <div className="space-y-1.5">
          <div className="text-[10px] text-ink-500 font-semibold">核心（全部满足）</div>
          <div className="space-y-0.5">
            {coreRules.map((r, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <span className={r.passed ? "text-emerald-400" : "text-red-400"}>
                  {r.passed ? "✓" : "✗"}
                </span>
                <span className="text-ink-300 flex-1">{r.desc || r.expr}</span>
                {r.value != null && (
                  <span className="text-ink-500 font-mono text-[10px]">{typeof r.value === "number" ? r.value.toFixed(2) : r.value}</span>
                )}
              </div>
            ))}
          </div>
          <div className="text-[10px] text-ink-500 font-semibold">可选（{optHit}/{optRules.length}，需≥{side.min_match ?? 1}）</div>
          <div className="space-y-0.5">
            {optRules.map((r, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <span className={r.passed ? "text-emerald-400" : "text-red-400"}>
                  {r.passed ? "✓" : "✗"}
                </span>
                <span className="text-ink-300 flex-1">{r.desc || r.expr}</span>
                {r.value != null && (
                  <span className="text-ink-500 font-mono text-[10px]">{typeof r.value === "number" ? r.value.toFixed(2) : r.value}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-0.5">
          {side.rules.map((r, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <span className={r.passed ? "text-emerald-400" : "text-red-400"}>
                {r.passed ? "✓" : "✗"}
              </span>
              <span className="text-ink-300 flex-1">{r.desc || r.expr}</span>
              {r.value != null && (
                <span className="text-ink-500 font-mono text-[10px]">{typeof r.value === "number" ? r.value.toFixed(2) : r.value}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
