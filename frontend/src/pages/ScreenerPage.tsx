import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { ScreenerItem, ScreenerResponse, SyncTask } from "../types";

const PATTERNS = [
  { key: "breakout_pullback", label: "突破回踩", color: "gold" },
  { key: "stabilize", label: "下跌企稳", color: "sky" },
  { key: "box_support", label: "箱体支撑", color: "gold" },
  { key: "volume_breakout", label: "放量突破", color: "emerald" },
  { key: "macd_divergence", label: "MACD底背离", color: "violet" },
];

type SortKey = "score" | "change_pct" | "volume_ratio" | "distance" | "price" | "rr_ratio" | "support_score" | "amount";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; defaultDir: SortDir }[] = [
  { key: "score", label: "信号强度", defaultDir: "desc" },
  { key: "price", label: "现价", defaultDir: "desc" },
  { key: "change_pct", label: "涨跌幅", defaultDir: "desc" },
  { key: "amount", label: "成交额", defaultDir: "desc" },
  { key: "volume_ratio", label: "量比", defaultDir: "desc" },
  { key: "distance", label: "距支撑", defaultDir: "asc" },
  { key: "rr_ratio", label: "盈亏比", defaultDir: "desc" },
  { key: "support_score", label: "支撑强度", defaultDir: "desc" },
];

export function ScreenerPage({ onPickStock }: { onPickStock: (code: string) => void }) {
  const [pattern, setPattern] = useState("breakout_pullback");
  const [data, setData] = useState<Record<string, ScreenerResponse>>({});
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<{ processed: number; total: number } | null>(null);
  const [minScore, setMinScore] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>(() => (localStorage.getItem("screener_sortKey") as SortKey) || "score");
  const [sortDir, setSortDir] = useState<SortDir>(() => (localStorage.getItem("screener_sortDir") as SortDir) || "desc");
  const [history, setHistory] = useState<{ ts: string; scanned_at: string; total: number; scanned: number }[]>([]);
  const [histOpen, setHistOpen] = useState(false);
  const [activeTs, setActiveTs] = useState<string | null>(null); // null = latest
  const histRef = useRef<HTMLDivElement>(null);

  const fetchResults = useCallback(() => {
    setLoading(true);
    Promise.all(
      PATTERNS.map((p) => api.screener(p.key, 200))
    )
      .then((results) => {
        const obj: Record<string, ScreenerResponse> = {};
        PATTERNS.forEach((p, i) => { obj[p.key] = results[i]; });
        setData(obj);
      })
      .finally(() => setLoading(false));
  }, []);

  const fetchHistory = useCallback(() => {
    api.screenerHistory(pattern).then(setHistory).catch(() => {});
  }, [pattern]);

  useEffect(() => { fetchResults(); }, [fetchResults]);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Close history dropdown on outside click
  useEffect(() => {
    if (!histOpen) return;
    const handler = (e: MouseEvent) => {
      if (histRef.current && !histRef.current.contains(e.target as Node)) setHistOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [histOpen]);

  const runScan = async () => {
    setScanning(true);
    setScanProgress(null);
    setActiveTs(null);
    try {
      await api.triggerScan();
      // Poll progress from sync tasks
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const tasks: SyncTask[] = await api.syncTasks();
          const screenerTask = tasks.find((t) => t.task_type === "screener");
          if (screenerTask) {
            if (screenerTask.total > 0) {
              setScanProgress({ processed: screenerTask.processed, total: screenerTask.total });
            }
            if (screenerTask.status === "done" || screenerTask.status === "error") {
              break;
            }
          }
        } catch {}
      }
      // Final fetch
      fetchResults();
      fetchHistory();
    } finally {
      setScanning(false);
      setScanProgress(null);
    }
  };

  const loadSnapshot = async (ts: string) => {
    setLoading(true);
    setActiveTs(ts);
    setHistOpen(false);
    try {
      const snap = await api.screenerSnapshot(pattern, ts, 200);
      setData((prev) => ({ ...prev, [pattern]: snap }));
    } catch {}
    setLoading(false);
  };

  const loadLatest = () => {
    setActiveTs(null);
    setHistOpen(false);
    fetchResults();
  };

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      const next = sortDir === "desc" ? "asc" : "desc";
      setSortDir(next);
      localStorage.setItem("screener_sortDir", next);
    } else {
      const col = COLUMNS.find((c) => c.key === key)!;
      setSortKey(key);
      setSortDir(col.defaultDir);
      localStorage.setItem("screener_sortKey", key);
      localStorage.setItem("screener_sortDir", col.defaultDir);
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
        case "amount": va = a.amount ?? 0; vb = b.amount ?? 0; break;
        case "rr_ratio": va = a.rr_ratio ?? 0; vb = b.rr_ratio ?? 0; break;
        case "support_score": va = a.support_score ?? 0; vb = b.support_score ?? 0; break;
        case "distance":
          va = a.distance_to_support_pct ?? 999;
          vb = b.distance_to_support_pct ?? 999;
          break;
        default: return 0;
      }
      return (va - vb) * mul;
    });
  }, [data, pattern, minScore, sortKey, sortDir]);

  const counts: Record<string, number> = {};
  PATTERNS.forEach((p) => { counts[p.key] = data[p.key]?.total ?? 0; });
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
                <span className="num text-ink-500 ml-1.5">{counts[p.key] ?? 0}</span>
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
          {/* History dropdown */}
          <div className="relative" ref={histRef}>
            <button
              className="px-2 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600 transition"
              onClick={() => { if (!histOpen) fetchHistory(); setHistOpen(!histOpen); }}
            >
              <i className="fas fa-clock-rotate-left mr-1 text-[9px]" />
              {activeTs ? `${activeTs.slice(4,6)}-${activeTs.slice(6,8)} ${activeTs.slice(9,11)}:${activeTs.slice(11)}` : "历史"}
              <i className="fas fa-chevron-down ml-1 text-[8px]" />
            </button>
            {histOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-64 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 max-h-72 overflow-y-auto scrollbar animate-in fade-in slide-in-from-top-1 duration-150">
                <button
                  className={`w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition flex items-center gap-2 ${!activeTs ? "text-gold" : "text-ink-300"}`}
                  onClick={loadLatest}
                >
                  <i className={`fas fa-${activeTs ? "arrow-left" : "check"} text-[9px] w-3`} />
                  <span className="font-medium">最新结果</span>
                </button>
                <div className="border-t border-ink-700 my-1" />
                {history.length === 0 && (
                  <div className="px-3 py-4 text-[10px] text-ink-600 text-center">暂无历史记录</div>
                )}
                {history.map((h) => (
                  <button
                    key={h.ts}
                    className={`w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition flex items-center justify-between ${activeTs === h.ts ? "text-gold bg-ink-800/50" : "text-ink-300"}`}
                    onClick={() => loadSnapshot(h.ts)}
                  >
                    <span>
                      {activeTs === h.ts && <i className="fas fa-check text-[9px] mr-1.5" />}
                      {h.scanned_at.replace("T", " ").slice(5, 16)}
                    </span>
                    <span className="text-ink-500 num">{h.total} 只入选</span>
                  </button>
                ))}
              </div>
            )}
          </div>
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

      {/* ── Progress bar ── */}
      {scanning && scanProgress && scanProgress.total > 0 && (
        <div className="px-5 py-2 border-b border-ink-800 bg-ink-900/50">
          <div className="flex items-center justify-between text-[11px] mb-1.5">
            <span className="text-ink-400">
              <i className="fas fa-circle-notch fa-spin mr-1.5 text-[9px] text-gold" />
              正在扫描...
            </span>
            <span className="num text-ink-300">
              {scanProgress.processed} / {scanProgress.total}
              <span className="text-ink-500 ml-1.5">
                ({Math.round(scanProgress.processed / scanProgress.total * 100)}%)
              </span>
            </span>
          </div>
          <div className="w-full h-1.5 bg-ink-800 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700 ease-out"
              style={{
                width: `${Math.max(1, scanProgress.processed / scanProgress.total * 100)}%`,
                background: "linear-gradient(90deg, #d4a857, #f0c674)",
              }}
            />
          </div>
        </div>
      )}

      {/* ── Results table ── */}
      <div className="overflow-y-auto scrollbar flex-1">
        <table className="w-full text-[12px] num">
          <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
            <tr className="border-b border-ink-800">
              <th className="text-left font-normal px-5 py-2.5 w-8">#</th>
              <th className="text-left font-normal px-2">代码 / 名称</th>
              <th className="text-left font-normal px-2">市场</th>
              <th className="text-left font-normal px-2">行业</th>
              <th className="text-left font-normal px-2">概念</th>
              <th className="text-left font-normal px-2">基本面</th>
              <SortTh k="price" label="现价" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="change_pct" label="涨跌幅" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="amount" label="成交额(万)" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="distance" label="距支撑" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="rr_ratio" label="盈亏比" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="support_score" label="支撑强度" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="volume_ratio" label="量比" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="score" label="信号强度" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" className="w-36" />
              <th className="text-left font-normal px-2">触发条件</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {(loading || scanning) && items.length === 0 && (
              <tr>
                <td colSpan={15} className="text-center text-ink-500 py-16">
                  <i className="fas fa-circle-notch fa-spin text-xl text-gold mb-3 block" />
                  {scanning
                    ? scanProgress && scanProgress.total > 0
                      ? `正在扫描 ${scanProgress.processed}/${scanProgress.total}...`
                      : "正在准备扫描，可能需要先更新行情数据..."
                    : "加载中..."}
                </td>
              </tr>
            )}
            {!loading && !scanning && items.length === 0 && (
              <tr>
                <td colSpan={15} className="text-center text-ink-500 py-16">
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
                  onClick={() => window.open(`/stock/${it.code}`, "_blank")}
                >
                  <td className="px-5 py-2.5 text-ink-500">{i + 1}</td>
                  <td className="px-2">
                    <div>
                      <span className="font-sans text-ink-100">{it.name}</span>
                      <span className="text-[10px] text-ink-500 ml-1.5">{it.code}</span>
                    </div>
                  </td>
                  <td className="px-2 text-ink-400 text-[11px]">{it.market || "—"}</td>
                  <td className="px-2 text-ink-400 text-[11px]">{it.industry || "—"}</td>
                  <td className="px-2 text-ink-400 text-[11px] max-w-[100px] truncate" title={it.concept || ""}>{it.concept || "—"}</td>
                  <td className="px-2 text-[11px]">
                    {it.fundamental_status === "healthy" ? (
                      <span className="text-cn-up" title={it.fundamental_summary}>良好</span>
                    ) : it.fundamental_status === "neutral" ? (
                      <span className="text-ink-300" title={it.fundamental_summary}>中性</span>
                    ) : it.fundamental_status === "weak" ? (
                      <span className="text-yellow-400" title={it.fundamental_summary}>偏弱</span>
                    ) : it.fundamental_status === "risk" ? (
                      <span className="text-cn-dn" title={it.fundamental_summary}>风险</span>
                    ) : (
                      <span className="text-ink-500">—</span>
                    )}
                  </td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {it.price.toFixed(2)}
                  </td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {up ? "+" : ""}{it.change_pct.toFixed(2)}%
                  </td>
                  <td className="text-right px-2 text-ink-300">
                    {it.amount != null ? it.amount.toLocaleString() : "—"}
                  </td>
                  <td className="text-right px-2 text-cn-dn">
                    {it.distance_to_support_pct != null
                      ? (it.distance_to_support_pct >= 0 ? "+" : "") + it.distance_to_support_pct.toFixed(2) + "%"
                      : "—"}
                  </td>
                  <td className="text-right px-2">
                    <span className={it.rr_ratio != null && it.rr_ratio >= 3 ? "text-gold" : "text-ink-300"}>
                      {it.rr_ratio != null ? it.rr_ratio.toFixed(1) : "—"}
                    </span>
                  </td>
                  <td className="text-right px-2">
                    <span className={it.support_score != null && it.support_score >= 70 ? "text-gold" : "text-ink-300"}>
                      {it.support_score != null ? Math.round(it.support_score) : "—"}
                    </span>
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
