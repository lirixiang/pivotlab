import { useState } from "react";
import type { ScreenerItem, ScreenerResponse } from "../types";
import { api } from "../services/api";

type Props = {
  onSelect: (code: string) => void;
  onResults?: (r: { breakout: ScreenerItem[]; bottom: ScreenerItem[] }) => void;
};

const PATTERNS = [
  { key: "breakout_pullback", label: "突破回踩", color: "bg-gold" },
  { key: "bottom_stabilize", label: "下跌企稳", color: "bg-sky2" },
];

export function ScreenerTable({ onSelect, onResults }: Props) {
  const [pattern, setPattern] = useState("breakout_pullback");
  const [data, setData] = useState<Record<string, ScreenerResponse>>({});
  const [loading, setLoading] = useState(false);

  async function runScan() {
    setLoading(true);
    try {
      // Trigger scan in subprocess
      await api.triggerScan();
      // Poll for results (subprocess writes cache files)
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const [bp, bs] = await Promise.all([
          api.screener("breakout_pullback"),
          api.screener("bottom_stabilize"),
        ]);
        if (bp.total > 0 || bs.total > 0 || i >= 29) {
          setData({ breakout_pullback: bp, bottom_stabilize: bs });
          onResults?.({ breakout: bp.items, bottom: bs.items });
          break;
        }
      }
    } finally {
      setLoading(false);
    }
  }

  const items = data[pattern]?.items ?? [];
  const isResistance = pattern === "breakout_pullback";

  return (
    <div className="border-t border-ink-800 grad-head">
      <div className="flex items-center justify-between px-5 py-2.5">
        <div className="flex items-center gap-3">
          <span className="text-[12px] font-semibold text-white tracking-wide">今日筛选结果</span>
          <div className="seg">
            {PATTERNS.map((p) => (
              <button
                key={p.key}
                className={pattern === p.key ? "on" : ""}
                onClick={() => setPattern(p.key)}
              >
                {p.label} · {data[p.key]?.total ?? "—"}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-ink-500">排序</span>
          <select className="bg-ink-850 border border-ink-700 rounded-md text-[12px] px-2 py-1 text-ink-200 focus:outline-none">
            <option>信号强度 ↓</option>
            <option>量比 ↓</option>
            <option>距支撑 ↑</option>
          </select>
          <button className="px-2.5 py-1 text-[12px] rounded-md bg-ink-800 ring-soft text-ink-200 hover:text-white">
            <i className="far fa-file-excel mr-1" />
            导出
          </button>
          <button
            className="px-3 py-1 text-[12px] rounded-md bg-sky-700 hover:bg-sky-600 text-white disabled:opacity-50"
            onClick={runScan}
            disabled={loading}
          >
            {loading ? (
              <><i className="fas fa-circle-notch fa-spin mr-1" />扫描中...</>
            ) : (
              <><i className="fas fa-search mr-1" />开始扫描</>
            )}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto max-h-[280px] scrollbar overflow-y-auto">
        <table className="w-full text-[12px] num">
          <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head">
            <tr className="border-y border-ink-800">
              <th className="text-left font-normal px-5 py-2">代码 / 名称</th>
              <th className="text-right font-normal px-2">现价</th>
              <th className="text-right font-normal px-2">涨跌</th>
              <th className="text-right font-normal px-2">突破/回踩</th>
              <th className="text-right font-normal px-2">距支撑</th>
              <th className="text-right font-normal px-2">量比</th>
              <th className="text-left font-normal px-2">信号强度</th>
              <th className="text-left font-normal px-2">触发条件</th>
              <th className="text-right font-normal px-5">操作</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {loading && (
              <tr>
                <td colSpan={9} className="text-center text-ink-500 py-6">
                  <i className="fas fa-circle-notch fa-spin mr-2" />扫描中...
                </td>
              </tr>
            )}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={9} className="text-center text-ink-500 py-6">
                  {Object.keys(data).length === 0
                    ? "点击「开始扫描」运行形态筛选"
                    : "当前没有符合条件的标的"}
                </td>
              </tr>
            )}
            {items.map((it, i) => {
              const up = it.change_pct >= 0;
              return (
                <tr key={it.code} className="row-hover border-b border-ink-850/70">
                  <td className="px-5 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className={"relative inline-block"}>
                        <span
                          className={
                            "dot " +
                            (isResistance ? "bg-gold" : "bg-sky2") +
                            (i === 0 ? " pulse-dot" : "")
                          }
                        />
                      </span>
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
                    <div className="flex items-center gap-2 w-32">
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
                      <span key={j} className={"chip mr-1 " + (isResistance ? "chip-on" : "chip-dn")}>
                        {t}
                      </span>
                    ))}
                  </td>
                  <td className="text-right pr-5">
                    <button
                      onClick={() => onSelect(it.code)}
                      className="text-gold hover:underline"
                    >
                      查看图表 →
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
