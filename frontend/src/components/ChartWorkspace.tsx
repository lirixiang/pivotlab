import type { StockDetail } from "../types";
import { ChartCanvas } from "./ChartCanvas";

type Props = {
  data: StockDetail | null;
  loading: boolean;
  period: string;
  onPeriodChange: (p: string) => void;
  refreshing?: boolean;
  onRefresh?: () => void;
  isWatched?: boolean;
  onToggleWatch?: () => void;
};

const PERIODS = ["1分", "5分", "30分", "日线", "周线", "月线"];

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

export function ChartWorkspace({ data, loading, period, onPeriodChange, refreshing, onRefresh, isWatched, onToggleWatch }: Props) {
  const q = data?.quote;
  const up = (q?.change_pct ?? 0) >= 0;
  const f = q?.fundamentals;
  const ac = q?.analyst_consensus;
  return (
    <section className="bg-ink-950 flex flex-col flex-1">
      {/* ─── Header bar ─── */}
      <div className="flex items-center px-5 py-3 border-b border-ink-800 grad-head gap-4">
        <div>
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
          <div className="flex items-baseline gap-3 mt-1">
            <span className={"num text-2xl font-semibold " + (up ? "text-cn-up" : "text-cn-dn")}>
              {q ? q.price.toFixed(2) : "—"}
            </span>
            {q && (
              <span className={"num text-sm " + (up ? "text-cn-up" : "text-cn-dn")}>
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

        <div className="flex-1" />

        <div className="seg">
          {PERIODS.map((p) => (
            <button key={p} className={p === period ? "on" : ""} onClick={() => onPeriodChange(p)}>
              {p}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <button className="chip chip-on flex items-center gap-1.5">
            <span className="dot bg-gold" />
            压力位
          </button>
          <button className="chip chip-on flex items-center gap-1.5">
            <span className="dot bg-sky2" />
            支撑位
          </button>
          <button className="chip flex items-center gap-1.5">
            <span className="dot bg-ink-500" />
            均线
          </button>
        </div>

        <button className="ml-2 px-3 py-1.5 rounded-md grad-gold text-ink-950 text-[12px] font-semibold flex items-center gap-1.5">
          <i className="fas fa-wand-magic-sparkles text-[11px]" /> 重新画线
        </button>

        <button
          className="ml-1 px-3 py-1.5 rounded-md bg-ink-800 ring-soft text-[12px] text-ink-200 hover:text-white flex items-center gap-1.5 disabled:opacity-50"
          onClick={onRefresh}
          disabled={refreshing || loading}
          title="从数据源刷新K线数据"
        >
          <i className={"fas fa-arrows-rotate text-[11px]" + (refreshing ? " fa-spin" : "")} />
          {refreshing ? "刷新中" : "刷新数据"}
        </button>
      </div>

      {/* ─── Main content: chart + side panel ─── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Chart area */}
        <div className="relative flex-1 px-5 pt-4 pb-2 overflow-hidden">
          {loading || !data ? (
            <div className="h-[560px] flex items-center justify-center text-ink-500 text-sm">
              <i className="fas fa-circle-notch fa-spin mr-2" /> 正在加载行情与画线...
            </div>
          ) : (
            <ChartCanvas candles={data.candles} levels={data.levels} consensus={q?.analyst_consensus} />
          )}

          <div className="absolute left-2 top-1/2 -translate-y-1/2 flex flex-col gap-1 bg-ink-850 hairline rounded-md p-1">
            {[
              { i: "fa-crosshairs", t: "十字光标" },
              { i: "fa-wave-square", t: "自动画线", on: true },
              { i: "fa-arrow-trend-up", t: "趋势线" },
              { i: "fa-percent", t: "斐波" },
              { i: "fa-layer-group", t: "筹码" },
            ].map((b, i) => (
              <button
                key={i}
                title={b.t}
                className={
                  "w-7 h-7 rounded text-xs flex items-center justify-center " +
                  (b.on
                    ? "text-gold bg-ink-700"
                    : "text-ink-500 hover:text-white hover:bg-ink-700")
                }
              >
                <i className={"fas " + b.i + " text-xs"} />
              </button>
            ))}
          </div>
        </div>

        {/* ─── Side info panel (like SR KLineAnalysisPanel) ─── */}
        {q && (
          <aside className="w-[280px] flex-shrink-0 border-l border-ink-800 bg-ink-900/50 overflow-y-auto px-4 py-4 text-[12px] space-y-3">
            {/* Stock headline */}
            <div>
              <div className="text-white font-semibold text-[14px]">{q.name}</div>
              <div className="text-ink-500 text-[11px] mt-0.5">
                {q.code} · {q.industry || "行业待同步"} · {q.market || ""}
              </div>
            </div>

            {/* Concepts */}
            <div className="flex flex-wrap gap-1.5">
              {q.concepts && q.concepts.length > 0
                ? q.concepts.slice(0, 5).map((c) => (
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
