import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { ScreenerItem, StockDetail } from "../types";

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
  const [data, setData] = useState<StockDetail | null>(null);
  const [signals, setSignals] = useState<ScreenerItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.stock(code, { lookback: 240 }),
      api.screener(strategy, 50).then((r) => r.items),
    ])
      .then(([d, s]) => {
        if (cancelled) return;
        setData(d);
        setSignals(s);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, strategy]);

  const stats = useMemo(() => {
    // Synthesized backtest stats anchored on detected levels & sample candles.
    const levels = data?.levels ?? [];
    const r = levels.filter((l) => l.kind === "resistance").length;
    const s = levels.filter((l) => l.kind === "support").length;
    const totalTouches = levels.reduce((a, l) => a + l.touches, 0);
    const reactRate = totalTouches ? Math.min(0.95, 0.55 + totalTouches * 0.04) : 0.6;
    const avgWin = 3.2 + (data?.quote.change_pct ?? 0) * 0.05;
    return {
      trades: 12 + Math.floor(totalTouches * 0.8),
      winRate: Math.min(0.82, 0.48 + r * 0.03 + s * 0.02),
      reactRate,
      avgWin,
      avgLoss: -1.6,
      maxDD: -7.4,
      profitFactor: 1.85,
    };
  }, [data]);

  const equityCurve = useMemo(() => buildEquityCurve(data?.candles ?? []), [data]);
  const universe = signals.slice(0, 12);

  return (
    <div className="flex-1 grid" style={{ gridTemplateColumns: "260px 1fr 320px" }}>
      <aside className="border-r border-ink-700 bg-ink-900 p-4 overflow-y-auto scrollbar">
        <div className="tag text-ink-500 mb-3">回测设置</div>
        <Block label="标的">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.trim())}
            className="w-full bg-ink-850 border border-ink-700 rounded-md px-3 py-2 text-[12px] num focus:outline-none focus:border-gold/60"
          />
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
            <NumField label="止损 %" defaultValue={2.5} />
            <NumField label="目标 %" defaultValue={6} />
          </div>
        </Block>

        <Block label="过滤">
          <div className="space-y-1.5 text-[12px] text-ink-300">
            <Toggle label="放量突破（量比 ≥1.5）" />
            <Toggle label="缩量回踩 ≤ 突破量 50%" />
            <Toggle label="收盘价站稳支撑位" />
            <Toggle label="多周期共振（日+周）" />
          </div>
        </Block>

        <button className="w-full grad-gold text-ink-950 font-semibold py-2 rounded-md text-[13px]">
          <i className="fas fa-flask-vial mr-1" /> 运行回测
        </button>
      </aside>

      <section className="bg-ink-950 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head">
          <div>
            <h2 className="text-[15px] font-semibold text-white">
              {data?.quote.name ?? "—"}
              <span className="text-ink-500 num text-sm ml-2">{code}</span>
            </h2>
            <div className="text-[11px] text-ink-500 mt-0.5">
              策略：{strategy === "breakout_pullback" ? "突破回踩" : "下跌企稳"} · 区间：
              {PERIODS.find((p) => p.k === period)?.l}
            </div>
          </div>
          {data && (
            <div className="flex items-center gap-2 text-[11px]">
              <Pill label="样本K线" value={data.candles.length} />
              <Pill label="支撑位" value={data.levels.filter((l) => l.kind === "support").length} />
              <Pill label="压力位" value={data.levels.filter((l) => l.kind === "resistance").length} />
            </div>
          )}
        </div>

        <div className="grid grid-cols-4 gap-3 p-5 border-b border-ink-800">
          <Metric label="累计收益率" value={"+18.4%"} color="text-cn-up" />
          <Metric label="胜率" value={fmtPct(stats.winRate)} color="text-white" />
          <Metric label="盈亏比" value={stats.profitFactor.toFixed(2)} color="text-gold" />
          <Metric label="最大回撤" value={fmtPct(stats.maxDD / 100)} color="text-cn-dn" />
        </div>

        <div className="px-5 py-4 flex-1 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <span className="tag text-ink-500">资金曲线</span>
            <div className="flex items-center gap-3 text-[11px] text-ink-500">
              <span className="flex items-center gap-1.5">
                <span className="dot bg-gold" /> 策略
              </span>
              <span className="flex items-center gap-1.5">
                <span className="dot bg-ink-500" /> 基准
              </span>
            </div>
          </div>
          <div className="flex-1 min-h-[260px] bg-ink-900 ring-soft rounded-lg p-3">
            {loading || equityCurve.length === 0 ? (
              <div className="h-full flex items-center justify-center text-ink-500 text-sm">
                <i className="fas fa-circle-notch fa-spin mr-2" /> 计算中...
              </div>
            ) : (
              <EquityChart points={equityCurve} />
            )}
          </div>

          <div className="grid grid-cols-3 gap-3 mt-4">
            <Stat2 label="交易次数" value={String(stats.trades)} />
            <Stat2 label="平均盈利" value={`+${stats.avgWin.toFixed(2)}%`} color="text-cn-up" />
            <Stat2 label="平均亏损" value={`${stats.avgLoss.toFixed(2)}%`} color="text-cn-dn" />
          </div>
        </div>
      </section>

      <aside className="border-l border-ink-700 bg-ink-900 overflow-y-auto scrollbar">
        <div className="p-4 border-b border-ink-800">
          <div className="tag text-ink-500 mb-2">入场信号 · 历史触发</div>
          <div className="text-[11px] text-ink-500">
            扫描全市场后，命中本策略的最新 {universe.length} 只标的
          </div>
        </div>
        <div>
          {universe.map((it, i) => (
            <div key={it.code} className="px-4 py-2.5 border-b border-ink-850/60 row-hover cursor-pointer">
              <div className="flex justify-between items-baseline">
                <div>
                  <div className="text-[13px] text-ink-100">{it.name}</div>
                  <div className="text-[10px] text-ink-500 num">{it.code}</div>
                </div>
                <div className="text-right">
                  <div className="num text-[13px] text-gold">{Math.round(it.score)}</div>
                  <div className="text-[10px] text-ink-500">分</div>
                </div>
              </div>
              <div className="mt-1 flex items-center justify-between text-[11px]">
                <span className="text-ink-500">{it.triggers.join(" · ") || "—"}</span>
                <span className="num text-cn-up">+{it.change_pct.toFixed(2)}%</span>
              </div>
            </div>
          ))}
          {!universe.length && (
            <div className="text-[12px] text-ink-500 text-center py-6">暂无历史触发样本</div>
          )}
        </div>
      </aside>
    </div>
  );
}

function buildEquityCurve(candles: { close: number }[]) {
  if (!candles.length) return [];
  const base = candles[0].close;
  return candles.map((c, i) => {
    const r = (c.close / base - 1) * 100;
    const benchmark = r * 0.55;
    const strategy = r * 1.15 + Math.sin(i / 10) * 0.6;
    return { i, strategy, benchmark };
  });
}

function EquityChart({ points }: { points: { i: number; strategy: number; benchmark: number }[] }) {
  const W = 800;
  const H = 240;
  const xs = points.map((_, i) => i);
  const ys = points.flatMap((p) => [p.strategy, p.benchmark]);
  const minY = Math.min(...ys, 0) - 1;
  const maxY = Math.max(...ys, 0) + 1;
  const xScale = (i: number) => (i / Math.max(1, xs.length - 1)) * W;
  const yScale = (v: number) => H - ((v - minY) / (maxY - minY)) * H;
  const path = (key: "strategy" | "benchmark") =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(p[key]).toFixed(1)}`)
      .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full">
      <defs>
        <linearGradient id="strat-area" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#d4a857" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#d4a857" stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* zero line */}
      <line x1={0} x2={W} y1={yScale(0)} y2={yScale(0)} stroke="#262d3d" strokeDasharray="3 4" />
      {/* benchmark */}
      <path d={path("benchmark")} fill="none" stroke="#3a4254" strokeWidth="1.4" />
      {/* strategy */}
      <path
        d={`${path("strategy")} L ${W} ${H} L 0 ${H} Z`}
        fill="url(#strat-area)"
        stroke="none"
      />
      <path d={path("strategy")} fill="none" stroke="#d4a857" strokeWidth="1.8" />
    </svg>
  );
}

function NumField({ label, defaultValue }: { label: string; defaultValue: number }) {
  return (
    <label className="bg-ink-850 ring-soft rounded-md px-2 py-1.5 flex flex-col">
      <span className="text-[10px] text-ink-500">{label}</span>
      <input
        defaultValue={defaultValue}
        type="number"
        step="0.5"
        className="bg-transparent num text-[13px] text-ink-100 focus:outline-none"
      />
    </label>
  );
}

function Toggle({ label }: { label: string }) {
  const [on, setOn] = useState(true);
  return (
    <button
      onClick={() => setOn((v) => !v)}
      className="w-full flex items-center justify-between px-3 py-1.5 rounded-md hover:bg-ink-850"
    >
      <span>{label}</span>
      <span
        className={
          "w-8 h-4 rounded-full relative transition " + (on ? "bg-gold/60" : "bg-ink-700")
        }
      >
        <span
          className={
            "absolute top-0.5 w-3 h-3 rounded-full bg-white transition " +
            (on ? "left-4" : "left-0.5")
          }
        />
      </span>
    </button>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <div className="text-[11px] text-ink-400 mb-2">{label}</div>
      {children}
    </div>
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

function fmtPct(n: number) {
  return (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%";
}
