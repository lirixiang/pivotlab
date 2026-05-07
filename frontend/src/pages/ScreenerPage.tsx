import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { ScreenerItem, ScreenerResponse } from "../types";

const PATTERNS = [
  { key: "breakout_pullback", label: "突破回踩", color: "gold" },
  { key: "bottom_stabilize", label: "下跌企稳", color: "sky" },
];

type SortKey = "score" | "change_pct" | "volume_ratio" | "distance" | "price";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; defaultDir: SortDir }[] = [
  { key: "score", label: "信号强度", defaultDir: "desc" },
  { key: "price", label: "现价", defaultDir: "desc" },
  { key: "change_pct", label: "涨跌", defaultDir: "desc" },
  { key: "volume_ratio", label: "量比", defaultDir: "desc" },
  { key: "distance", label: "距支撑", defaultDir: "asc" },
];

export function ScreenerPage({ onPickStock }: { onPickStock: (code: string) => void }) {
  const [pattern, setPattern] = useState("breakout_pullback");
  const [data, setData] = useState<Record<string, ScreenerResponse>>({});
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [minScore, setMinScore] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const fetchResults = () => {
    setLoading(true);
    Promise.all([
      api.screener("breakout_pullback", 200),
      api.screener("bottom_stabilize", 200),
    ])
      .then(([bp, bs]) => setData({ breakout_pullback: bp, bottom_stabilize: bs }))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchResults(); }, []);

  const runScan = async () => {
    setScanning(true);
    try {
      await api.triggerScan();
      // Poll for results
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const [bp, bs] = await Promise.all([
          api.screener("breakout_pullback", 200),
          api.screener("bottom_stabilize", 200),
        ]);
        if (bp.total > 0 || bs.total > 0 || i >= 59) {
          setData({ breakout_pullback: bp, bottom_stabilize: bs });
          break;
        }
      }
    } finally {
      setScanning(false);
    }
  };

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      const col = COLUMNS.find((c) => c.key === key)!;
      setSortKey(key);
      setSortDir(col.defaultDir);
    }
  };

  const items: ScreenerItem[] = useMemo(() => {
    const raw = data[pattern]?.items ?? [];
    const filtered = minScore > 0 ? raw.filter((i) => i.score >= minScore) : raw;
    const mul = sortDir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => {
      let va: number, vb: number;
      switch (sortKey) {
        case "score": va = a.score; vb = b.score; break;
        case "price": va = a.price; vb = b.price; break;
        case "change_pct": va = a.change_pct; vb = b.change_pct; break;
        case "volume_ratio": va = a.volume_ratio; vb = b.volume_ratio; break;
        case "distance":
          va = a.distance_to_support_pct ?? 999;
          vb = b.distance_to_support_pct ?? 999;
          break;
        default: return 0;
      }
      return (va - vb) * mul;
    });
  }, [data, pattern, minScore, sortKey, sortDir]);

  const counts = {
    bp: data.breakout_pullback?.total ?? 0,
    bs: data.bottom_stabilize?.total ?? 0,
  };
  const scannedAt = data[pattern]?.scanned_at;
  const scanned = data[pattern]?.scanned ?? 0;
  const isBreakout = pattern === "breakout_pullback";
  const high = items.filter((i) => i.score >= 80).length;
  const med = items.filter((i) => i.score >= 60 && i.score < 80).length;

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      {/* ── Top toolbar ── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head flex-wrap gap-y-2">
        <div className="flex items-center gap-4">
          {/* Pattern tabs */}
          <div className="seg">
            {PATTERNS.map((p) => (
              <button
                key={p.key}
                className={pattern === p.key ? "on" : ""}
                onClick={() => setPattern(p.key)}
              >
                <span className={"dot mr-1.5 " + (p.color === "gold" ? "bg-gold" : "bg-sky2")} />
                {p.label}
                <span className="num text-ink-500 ml-1.5">{p.key === "breakout_pullback" ? counts.bp : counts.bs}</span>
              </button>
            ))}
          </div>

          {/* Min score filter */}
          <div className="flex items-center gap-2 text-[11px] text-ink-400">
            <span>≥</span>
            <input
              type="range" min={0} max={100} step={5} value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="w-20 accent-gold"
            />
            <span className="num text-ink-200 w-5 text-right">{minScore}</span>
            <span>分</span>
          </div>

          {/* Stats pills */}
          <div className="flex items-center gap-2 text-[11px]">
            <span className="chip"><span className="text-gold num mr-1">{high}</span> 高强</span>
            <span className="chip"><span className="text-sky2 num mr-1">{med}</span> 中等</span>
            <span className="chip"><span className="text-ink-100 num mr-1">{items.length}</span> 入选</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {scannedAt && (
            <span className="text-[10px] text-ink-500">
              上次扫描 {scannedAt.replace("T", " ").slice(5, 16)} · {scanned} 只
            </span>
          )}
          <button
            className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold disabled:opacity-50"
            onClick={runScan}
            disabled={scanning}
          >
            {scanning ? (
              <><i className="fas fa-circle-notch fa-spin mr-1" /> 扫描中...</>
            ) : (
              <><i className="fas fa-bolt mr-1" /> 全市场扫描</>
            )}
          </button>
        </div>
      </div>

      {/* ── Results table ── */}
      <div className="overflow-y-auto scrollbar flex-1">
        <table className="w-full text-[12px] num">
          <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
            <tr className="border-b border-ink-800">
              <th className="text-left font-normal px-5 py-2.5 w-8">#</th>
              <th className="text-left font-normal px-2">代码 / 名称</th>
              <SortTh k="price" label="现价" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="change_pct" label="涨跌" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <th className="text-right font-normal px-2">
                {isBreakout ? "突破/回踩" : "支撑位"}
              </th>
              <SortTh k="distance" label="距支撑" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="volume_ratio" label="量比" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="score" label="信号强度" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" className="w-44" />
              <th className="text-left font-normal px-2">触发条件</th>
              <th className="text-right font-normal px-5">操作</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {(loading || scanning) && items.length === 0 && (
              <tr>
                <td colSpan={10} className="text-center text-ink-500 py-16">
                  <i className="fas fa-circle-notch fa-spin text-xl text-gold mb-3 block" />
                  {scanning ? "正在扫描全市场，请稍候..." : "加载中..."}
                </td>
              </tr>
            )}
            {!loading && !scanning && items.length === 0 && (
              <tr>
                <td colSpan={10} className="text-center text-ink-500 py-16">
                  <i className="fas fa-binoculars text-2xl text-ink-700 block mb-3" />
                  <div>暂无结果</div>
                  <div className="mt-1 text-[11px]">
                    点击「全市场扫描」开始扫描，或降低最低信号强度
                  </div>
                </td>
              </tr>
            )}
            {items.map((it, i) => {
              const up = it.change_pct >= 0;
              const scoreColor = it.score >= 80 ? "text-gold" : it.score >= 60 ? "text-sky2" : "text-ink-400";
              const barColor = isBreakout
                ? "linear-gradient(90deg,#d4a857,#f0c674)"
                : "linear-gradient(90deg,#7dd3fc,#bae6fd)";
              return (
                <tr
                  key={it.code}
                  className="row-hover border-b border-ink-850/70 cursor-pointer"
                  onClick={() => onPickStock(it.code)}
                >
                  <td className="px-5 py-2.5 text-ink-500">{i + 1}</td>
                  <td className="px-2">
                    <div>
                      <span className="font-sans text-ink-100">{it.name}</span>
                      <span className="text-[10px] text-ink-500 ml-1.5">{it.code}</span>
                    </div>
                  </td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {it.price.toFixed(2)}
                  </td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {up ? "+" : ""}{it.change_pct.toFixed(2)}%
                  </td>
                  <td className="text-right px-2">
                    {isBreakout ? (
                      <>
                        {it.breakout_price ? it.breakout_price.toFixed(2) : "—"}
                        <span className="text-ink-600 mx-0.5">/</span>
                        {it.pullback_price ? it.pullback_price.toFixed(2) : "—"}
                      </>
                    ) : (
                      it.pullback_price ? it.pullback_price.toFixed(2) : "—"
                    )}
                  </td>
                  <td className="text-right px-2 text-cn-dn">
                    {it.distance_to_support_pct != null
                      ? (it.distance_to_support_pct >= 0 ? "+" : "") + it.distance_to_support_pct.toFixed(2) + "%"
                      : "—"}
                  </td>
                  <td className="text-right px-2">
                    <span className={it.volume_ratio >= 1.5 ? "text-gold" : ""}>
                      {it.volume_ratio.toFixed(2)}
                    </span>
                  </td>
                  <td className="px-2">
                    <div className="flex items-center gap-2">
                      <div className="level-bar flex-1 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: Math.min(100, it.score) + "%", background: barColor }}
                        />
                      </div>
                      <span className={"w-6 text-right " + scoreColor}>{Math.round(it.score)}</span>
                    </div>
                  </td>
                  <td className="px-2">
                    <div className="flex flex-wrap gap-1">
                      {it.triggers.map((t, j) => (
                        <span key={j} className={"chip text-[10px] " + (isBreakout ? "chip-on" : "chip-dn")}>
                          {t}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="text-right pr-5">
                    <button className="text-gold hover:underline text-[11px]">
                      查看 <i className="fas fa-arrow-right text-[9px]" />
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

/* ── Sortable column header ── */
function SortTh({ k, label, sortKey, sortDir, onClick, align = "left", className = "" }: {
  k: SortKey; label: string; sortKey: SortKey; sortDir: SortDir;
  onClick: (k: SortKey) => void; align?: "left" | "right"; className?: string;
}) {
  const active = sortKey === k;
  return (
    <th
      className={
        "font-normal px-2 py-2.5 cursor-pointer select-none transition " +
        (align === "right" ? "text-right " : "text-left ") +
        (active ? "text-ink-200" : "text-ink-500 hover:text-ink-300") +
        (className ? " " + className : "")
      }
      onClick={() => onClick(k)}
    >
      {label}
      {active && (
        <i className={"fas fa-caret-" + (sortDir === "desc" ? "down" : "up") + " ml-1 text-[9px] text-gold"} />
      )}
    </th>
  );
}
