import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { ScreenerItem, ScreenerResponse } from "../types";

const PATTERNS = [
  { key: "breakout_pullback", label: "突破回踩", color: "gold" },
  { key: "bottom_stabilize", label: "下跌企稳", color: "sky" },
];

const PRESETS = [
  { key: "all", label: "全市场" },
  { key: "main", label: "主板" },
  { key: "star", label: "科创板" },
  { key: "cyb", label: "创业板" },
  { key: "watch", label: "自选股" },
];

export function ScreenerPage({ onPickStock }: { onPickStock: (code: string) => void }) {
  const [pattern, setPattern] = useState("breakout_pullback");
  const [data, setData] = useState<Record<string, ScreenerResponse>>({});
  const [loading, setLoading] = useState(false);
  const [minScore, setMinScore] = useState(60);
  const [sortKey, setSortKey] = useState<"score" | "vol" | "dist">("score");
  const [universe, setUniverse] = useState("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.screener("breakout_pullback", 200),
      api.screener("bottom_stabilize", 200),
    ])
      .then(([bp, bs]) => {
        if (!cancelled) setData({ breakout_pullback: bp, bottom_stabilize: bs });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const items: ScreenerItem[] = useMemo(() => {
    const raw = data[pattern]?.items ?? [];
    const filtered = raw.filter((i) => i.score >= minScore);
    const sorted = [...filtered].sort((a, b) => {
      if (sortKey === "vol") return b.volume_ratio - a.volume_ratio;
      if (sortKey === "dist")
        return (a.distance_to_support_pct ?? 999) - (b.distance_to_support_pct ?? 999);
      return b.score - a.score;
    });
    return sorted;
  }, [data, pattern, minScore, sortKey]);

  const counts = {
    bp: data.breakout_pullback?.total ?? 0,
    bs: data.bottom_stabilize?.total ?? 0,
  };
  const high = items.filter((i) => i.score >= 80).length;
  const med = items.filter((i) => i.score >= 60 && i.score < 80).length;
  const isResistance = pattern === "breakout_pullback";

  return (
    <div className="flex-1 grid" style={{ gridTemplateColumns: "260px 1fr" }}>
      {/* Filter rail */}
      <aside className="border-r border-ink-700 bg-ink-900 p-4 overflow-y-auto scrollbar">
        <div className="tag text-ink-500 mb-3">扫描配置</div>

        <Block label="形态类型">
          <div className="space-y-1.5">
            {PATTERNS.map((p) => (
              <button
                key={p.key}
                onClick={() => setPattern(p.key)}
                className={
                  "w-full flex items-center justify-between px-3 py-2 rounded-md text-[12px] transition " +
                  (pattern === p.key
                    ? "bg-ink-800 text-white ring-soft"
                    : "text-ink-300 hover:bg-ink-850")
                }
              >
                <span className="flex items-center gap-2">
                  <span className={"dot " + (p.color === "gold" ? "bg-gold" : "bg-sky2")} />
                  {p.label}
                </span>
                <span className="num text-ink-500">
                  {p.key === "breakout_pullback" ? counts.bp : counts.bs}
                </span>
              </button>
            ))}
          </div>
        </Block>

        <Block label="股票池">
          <div className="grid grid-cols-2 gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                onClick={() => setUniverse(p.key)}
                className={
                  "px-2 py-1.5 rounded-md text-[12px] " +
                  (universe === p.key
                    ? "bg-ink-800 text-white ring-soft"
                    : "text-ink-400 bg-ink-850 hover:text-white")
                }
              >
                {p.label}
              </button>
            ))}
          </div>
        </Block>

        <Block label={`最低信号强度  ${minScore}`}>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-full accent-gold"
          />
          <div className="flex justify-between text-[10px] text-ink-500 mt-1">
            <span>0</span>
            <span>50</span>
            <span>100</span>
          </div>
        </Block>

        <Block label="排序">
          <div className="space-y-1">
            {[
              { k: "score", l: "信号强度 ↓" },
              { k: "vol", l: "量比 ↓" },
              { k: "dist", l: "距离支撑 ↑" },
            ].map((o) => (
              <button
                key={o.k}
                onClick={() => setSortKey(o.k as any)}
                className={
                  "w-full text-left px-3 py-1.5 rounded-md text-[12px] " +
                  (sortKey === o.k
                    ? "bg-ink-800 text-white ring-soft"
                    : "text-ink-400 hover:bg-ink-850")
                }
              >
                {o.l}
              </button>
            ))}
          </div>
        </Block>

        <div className="mt-6 grid grid-cols-3 gap-2 text-center">
          <Stat label="高强" value={high} color="text-gold" />
          <Stat label="中等" value={med} color="text-sky2" />
          <Stat label="入选" value={items.length} color="text-white" />
        </div>
      </aside>

      {/* Main result */}
      <section className="flex flex-col bg-ink-950 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head">
          <div>
            <h2 className="text-[15px] font-semibold text-white">
              {PATTERNS.find((p) => p.key === pattern)?.label} · 全市场扫描
            </h2>
            <div className="text-[11px] text-ink-500 mt-0.5">
              共 {items.length} 只 · 入选阈值 ≥ {minScore} 分 · 数据 akshare · 实时回算
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button className="px-3 py-1.5 text-[12px] rounded-md bg-ink-800 ring-soft text-ink-200 hover:text-white">
              <i className="far fa-file-excel mr-1" /> 导出全部
            </button>
            <button className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold">
              <i className="fas fa-bolt mr-1" /> 立即重新扫描
            </button>
          </div>
        </div>

        <div className="overflow-y-auto scrollbar flex-1">
          <table className="w-full text-[12px] num">
            <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
              <tr className="border-b border-ink-800">
                <th className="text-left font-normal px-5 py-2.5">代码 / 名称</th>
                <th className="text-right font-normal px-2">现价</th>
                <th className="text-right font-normal px-2">涨跌</th>
                <th className="text-right font-normal px-2">突破/回踩</th>
                <th className="text-right font-normal px-2">距支撑</th>
                <th className="text-right font-normal px-2">量比</th>
                <th className="text-left font-normal px-2 w-40">信号强度</th>
                <th className="text-left font-normal px-2">触发条件</th>
                <th className="text-right font-normal px-5">操作</th>
              </tr>
            </thead>
            <tbody className="text-ink-200">
              {loading && (
                <tr>
                  <td colSpan={9} className="text-center text-ink-500 py-10">
                    <i className="fas fa-circle-notch fa-spin mr-2" /> 扫描中...
                  </td>
                </tr>
              )}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={9} className="text-center text-ink-500 py-10">
                    没有满足当前筛选条件的标的
                  </td>
                </tr>
              )}
              {items.map((it, i) => {
                const up = it.change_pct >= 0;
                return (
                  <tr
                    key={it.code}
                    className="row-hover border-b border-ink-850/70 cursor-pointer"
                    onClick={() => onPickStock(it.code)}
                  >
                    <td className="px-5 py-2.5">
                      <div className="flex items-center gap-2">
                        <span
                          className={
                            "dot " +
                            (isResistance ? "bg-gold" : "bg-sky2") +
                            (i === 0 ? " pulse-dot relative" : "")
                          }
                        />
                        <div>
                          <div className="font-sans text-ink-100">{it.name}</div>
                          <div className="text-[10px] text-ink-500">{it.code}</div>
                        </div>
                      </div>
                    </td>
                    <td className={"text-right " + (up ? "text-cn-up" : "text-cn-dn")}>
                      {it.price.toFixed(2)}
                    </td>
                    <td className={"text-right " + (up ? "text-cn-up" : "text-cn-dn")}>
                      {up ? "+" : ""}
                      {it.change_pct.toFixed(2)}%
                    </td>
                    <td className="text-right">
                      {it.breakout_price ? it.breakout_price.toFixed(2) : "—"} /{" "}
                      {it.pullback_price ? it.pullback_price.toFixed(2) : "—"}
                    </td>
                    <td className="text-right text-cn-dn">
                      {it.distance_to_support_pct != null
                        ? (it.distance_to_support_pct >= 0 ? "+" : "") +
                          it.distance_to_support_pct.toFixed(2) +
                          "%"
                        : "—"}
                    </td>
                    <td className="text-right">{it.volume_ratio.toFixed(2)}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <div className="level-bar flex-1">
                          <div
                            className="level-fill"
                            style={{
                              width: Math.min(100, it.score) + "%",
                              background: isResistance
                                ? "linear-gradient(90deg,#d4a857,#f0c674)"
                                : "linear-gradient(90deg,#7dd3fc,#bae6fd)",
                            }}
                          />
                        </div>
                        <span className={isResistance ? "text-gold" : "text-sky2"}>
                          {Math.round(it.score)}
                        </span>
                      </div>
                    </td>
                    <td>
                      {it.triggers.map((t, j) => (
                        <span
                          key={j}
                          className={"chip mr-1 " + (isResistance ? "chip-on" : "chip-dn")}
                        >
                          {t}
                        </span>
                      ))}
                    </td>
                    <td className="text-right pr-5">
                      <button className="text-gold hover:underline">查看 →</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
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

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-ink-850 ring-soft rounded-md py-2">
      <div className={"num text-base " + color}>{value}</div>
      <div className="text-[10px] text-ink-500 mt-0.5">{label}</div>
    </div>
  );
}
