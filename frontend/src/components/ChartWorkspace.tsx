import type { StockDetail } from "../types";
import { ChartCanvas } from "./ChartCanvas";

type Props = {
  data: StockDetail | null;
  loading: boolean;
  period: string;
  onPeriodChange: (p: string) => void;
};

const PERIODS = ["1分", "5分", "30分", "日线", "周线", "月线"];

export function ChartWorkspace({ data, loading, period, onPeriodChange }: Props) {
  const q = data?.quote;
  const up = (q?.change_pct ?? 0) >= 0;
  return (
    <section className="bg-ink-950 flex flex-col flex-1">
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
            <span className="chip">沪深300</span>
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
                成交 {(q.amount / 1e8).toFixed(1)}亿 · 量比 {q.volume_ratio.toFixed(2)} · 换手{" "}
                {q.turnover.toFixed(2)}%
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
      </div>

      <div className="relative px-5 pt-4 pb-2 overflow-hidden">
        {loading || !data ? (
          <div className="h-[560px] flex items-center justify-center text-ink-500 text-sm">
            <i className="fas fa-circle-notch fa-spin mr-2" /> 正在加载行情与画线...
          </div>
        ) : (
          <ChartCanvas candles={data.candles} levels={data.levels} />
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
    </section>
  );
}
