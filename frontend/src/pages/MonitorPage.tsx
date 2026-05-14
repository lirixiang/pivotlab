import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { WatchlistItem } from "../types";

type SortKey =
  | "name" | "industry" | "price" | "change_pct" | "amount"
  | "turnover_rate" | "pe" | "market_cap" | "roe"
  | "score" | "rr_ratio" | "distance_to_support_pct"
  | "fundamental" | "pattern" | "signal";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; defaultDir: SortDir; align: "left" | "right" }[] = [
  { key: "name",                    label: "名称/代码", defaultDir: "asc",  align: "left"  },
  { key: "industry",                label: "行业",      defaultDir: "asc",  align: "left"  },
  { key: "price",                   label: "现价",      defaultDir: "desc", align: "right" },
  { key: "change_pct",              label: "涨跌幅",    defaultDir: "desc", align: "right" },
  { key: "amount",                  label: "成交额",    defaultDir: "desc", align: "right" },
  { key: "turnover_rate",           label: "换手率",    defaultDir: "desc", align: "right" },
  { key: "pe",                      label: "PE",        defaultDir: "asc",  align: "right" },
  { key: "market_cap",              label: "市值",      defaultDir: "desc", align: "right" },
  { key: "roe",                     label: "ROE",       defaultDir: "desc", align: "right" },
  { key: "fundamental",             label: "基本面",    defaultDir: "desc", align: "left"  },
  { key: "pattern",                 label: "形态",      defaultDir: "desc", align: "left"  },
  { key: "signal",                  label: "信号",      defaultDir: "desc", align: "left"  },
  { key: "score",                   label: "评分",      defaultDir: "desc", align: "right" },
  { key: "rr_ratio",                label: "盈亏比",    defaultDir: "desc", align: "right" },
  { key: "distance_to_support_pct", label: "距支撑",    defaultDir: "asc",  align: "right" },
];

const FUND_RANK: Record<string, number> = { healthy: 4, neutral: 3, weak: 2, risk: 1, unknown: 0, "": 0 };
const FUND_LABEL: Record<string, string> = { healthy: "优", neutral: "中", weak: "弱", risk: "险", unknown: "—" };
const FUND_COLOR: Record<string, string> = {
  healthy: "text-green-400", neutral: "text-ink-300", weak: "text-amber-400",
  risk: "text-red-400", unknown: "text-ink-600",
};
const SIGNAL_RANK: Record<string, number> = { buy: 5, hold: 4, wait: 3, neutral: 2, sell: 1, "": 0 };
const SIGNAL_COLOR: Record<string, string> = {
  buy: "bg-red-500/20 text-red-400", sell: "bg-green-500/20 text-green-400",
  hold: "bg-amber-500/20 text-amber-400", wait: "bg-sky-500/15 text-sky-400",
  neutral: "text-ink-600",
};
const SIGNAL_ICON: Record<string, string> = {
  buy: "fa-arrow-up", sell: "fa-arrow-down",
  hold: "fa-pause", wait: "fa-clock",
};

const fmtAmount = (v: number | null | undefined) => {
  if (v == null || !v) return "—";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return v.toFixed(0);
};
const fmtMcap = (v: number | null | undefined) => {
  if (v == null || !v) return "—";
  if (v >= 1e12) return (v / 1e12).toFixed(2) + "万亿";
  if (v >= 1e8) return (v / 1e8).toFixed(0) + "亿";
  return fmtAmount(v);
};

function Sparkline({ data, up }: { data: number[]; up: boolean }) {
  if (!data || data.length < 2) return <span className="text-ink-700">—</span>;
  const w = 56, h = 18;
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || 1;
  const stepX = w / (data.length - 1);
  const pts = data.map((v, i) => `${(i * stepX).toFixed(1)},${(h - ((v - min) / span) * h).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} className="inline-block align-middle">
      <polyline fill="none" stroke={up ? "#22c55e" : "#ef4444"} strokeWidth="1.2" points={pts} />
    </svg>
  );
}

export function MonitorPage({ onPickStock }: { onPickStock: (code: string) => void }) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>(() => (localStorage.getItem("monitor_sortKey") as SortKey) || "change_pct");
  const [sortDir, setSortDir] = useState<SortDir>(() => (localStorage.getItem("monitor_sortDir") as SortDir) || "desc");

  // ── Toast notification ──
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const showToast = useCallback((msg: string, type: "success" | "error" | "info" = "success") => {
    clearTimeout(toastTimer.current);
    setToast({ msg, type });
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const [showAdd, setShowAdd] = useState(false);
  const [addQuery, setAddQuery] = useState("");
  const [addResults, setAddResults] = useState<{ code: string; name: string; industry: string }[]>([]);
  const [adding, setAdding] = useState("");
  const addRef = useRef<HTMLDivElement>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

  // ── Pattern filter chips (multi-select; empty = show all) ──
  const [patternFilter, setPatternFilter] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem("monitor_patternFilter");
      return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
    } catch { return new Set(); }
  });
  const togglePattern = (key: string) => {
    setPatternFilter((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      localStorage.setItem("monitor_patternFilter", JSON.stringify([...next]));
      return next;
    });
  };
  const clearPatternFilter = () => {
    setPatternFilter(new Set());
    localStorage.removeItem("monitor_patternFilter");
  };

  // ── Screenshot OCR import modal ──
  const [showOcr, setShowOcr] = useState(false);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrError, setOcrError] = useState("");
  const [ocrCandidates, setOcrCandidates] = useState<
    { code: string; name: string; industry: string; valid: boolean; in_watchlist: boolean; confidence: number; text: string }[]
  >([]);
  const [ocrSelected, setOcrSelected] = useState<Set<string>>(new Set());
  const [ocrPreview, setOcrPreview] = useState<string>("");
  const [importing, setImporting] = useState(false);
  const ocrFileRef = useRef<HTMLInputElement>(null);

  const resetOcr = () => {
    setOcrCandidates([]); setOcrSelected(new Set()); setOcrError("");
    if (ocrPreview) URL.revokeObjectURL(ocrPreview);
    setOcrPreview("");
  };
  const closeOcr = () => { setShowOcr(false); resetOcr(); };

  // ── Pattern scan (only against watchlist codes) ──
  const [scanning, setScanning] = useState(false);
  const handleScanPatterns = async () => {
    if (scanning) return;
    setScanning(true);
    try {
      const r = await api.scanWatchlistPatterns();
      load();
      const hitDesc = Object.entries(r.counts)
        .map(([k, v]) => `${r.labels[k] || k} ${v}`).join("、") || "无命中";
      showToast(`已扫描 ${r.scanned} 只，命中 ${r.total_hits} 项（${hitDesc}）`, "success");
    } catch (e) {
      showToast(`形态识别失败：${(e as Error).message || e}`, "error");
    } finally {
      setScanning(false);
    }
  };

  const load = useCallback(() => {
    setLoading(true);
    api.watchlist().then(setItems).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (addRef.current && !addRef.current.contains(e.target as Node)) {
        setShowAdd(false); setAddQuery(""); setAddResults([]);
      }
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  useEffect(() => {
    const q = addQuery.trim();
    if (!q) { setAddResults([]); return; }
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      api.searchStocks(q, 8).then(setAddResults).catch(() => {});
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [addQuery]);

  const handleAdd = async (code: string, name: string) => {
    setAdding(code);
    try {
      await api.addWatch(code, name);
      load();
      setAddQuery(""); setAddResults([]); setShowAdd(false);
    } finally { setAdding(""); }
  };

  const handleRemove = async (e: React.MouseEvent, code: string) => {
    e.stopPropagation();
    await api.removeWatch(code);
    setItems((prev) => prev.filter((i) => i.code !== code));
  };

  // ── OCR helpers ──
  const processOcrFile = useCallback(async (f: File) => {
    if (!f.type.startsWith("image/")) { setOcrError("请选择图片文件"); return; }
    resetOcr();
    setOcrPreview(URL.createObjectURL(f));
    setOcrLoading(true);
    try {
      const r = await api.ocrExtractCodes(f);
      setOcrCandidates(r.candidates);
      // pre-select all valid + not-yet-in-watchlist candidates
      setOcrSelected(new Set(r.candidates.filter((c) => c.valid && !c.in_watchlist).map((c) => c.code)));
      if (r.candidates.length === 0) setOcrError("未识别到股票代码");
    } catch (e) {
      setOcrError(String((e as Error).message || e));
    } finally {
      setOcrLoading(false);
    }
  }, []);

  // Paste-from-clipboard handler — active only while modal is open.
  useEffect(() => {
    if (!showOcr) return;
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items || []).find((it) => it.type.startsWith("image/"));
      if (!item) return;
      const f = item.getAsFile();
      if (f) { e.preventDefault(); processOcrFile(f); }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [showOcr, processOcrFile]);

  const handleImportSelected = async () => {
    const codes = [...ocrSelected];
    if (codes.length === 0) return;
    setImporting(true);
    try {
      const r = await api.importWatchlist(codes);
      closeOcr();
      load();
      // simple toast via alert (project does not seem to have a toast lib in this page)
      showToast(`已导入 ${r.added} 只，跳过 ${r.skipped_existing} 已存在 · ${r.skipped_unknown} 未知`, "success");
    } catch (e) {
      setOcrError(String((e as Error).message || e));
    } finally {
      setImporting(false);
    }
  };

  const toggleOcrPick = (code: string) => {
    setOcrSelected((prev) => {
      const n = new Set(prev);
      if (n.has(code)) n.delete(code); else n.add(code);
      return n;
    });
  };

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      const next = sortDir === "desc" ? "asc" : "desc";
      setSortDir(next);
      localStorage.setItem("monitor_sortDir", next);
    } else {
      const col = COLUMNS.find((c) => c.key === key)!;
      setSortKey(key);
      setSortDir(col.defaultDir);
      localStorage.setItem("monitor_sortKey", key);
      localStorage.setItem("monitor_sortDir", col.defaultDir);
    }
  };

  const sorted = useMemo(() => {
    const mul = sortDir === "desc" ? -1 : 1;
    const filtered = patternFilter.size === 0
      ? items
      : items.filter((r) => r.pattern && patternFilter.has(r.pattern));
    const getVal = (r: WatchlistItem): number | string => {
      switch (sortKey) {
        case "name":          return r.name || r.code;
        case "industry":      return r.industry || "";
        case "price":         return r.price;
        case "change_pct":    return r.change_pct;
        case "amount":        return r.amount;
        case "turnover_rate": return r.turnover_rate;
        case "pe":            return r.pe ?? Number.POSITIVE_INFINITY;
        case "market_cap":    return r.market_cap;
        case "roe":           return r.roe ?? -Infinity;
        case "fundamental":   return FUND_RANK[r.fundamental_status] ?? 0;
        case "pattern":       return r.pattern ? 1 : 0;
        case "signal":        return SIGNAL_RANK[r.signal] ?? 0;
        case "score":         return r.score ?? -Infinity;
        case "rr_ratio":      return r.rr_ratio ?? -Infinity;
        case "distance_to_support_pct": return r.distance_to_support_pct ?? Number.POSITIVE_INFINITY;
      }
    };
    return [...filtered].sort((a, b) => {
      const va = getVal(a), vb = getVal(b);
      if (typeof va === "string" && typeof vb === "string") return va.localeCompare(vb) * mul;
      return ((va as number) - (vb as number)) * mul;
    });
  }, [items, sortKey, sortDir, patternFilter]);

  // Available patterns across the current watchlist (with counts) → drives chips.
  const patternStats = useMemo(() => {
    const m = new Map<string, { label: string; count: number }>();
    for (const r of items) {
      if (!r.pattern) continue;
      const cur = m.get(r.pattern);
      if (cur) cur.count += 1;
      else m.set(r.pattern, { label: r.pattern_label || r.pattern, count: 1 });
    }
    return [...m.entries()].sort((a, b) => b[1].count - a[1].count);
  }, [items]);
  const noPatternCount = useMemo(() => items.filter((r) => !r.pattern).length, [items]);

  const existingCodes = new Set(items.map((i) => i.code));


  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden relative">
      {/* Toast notification */}
      {toast && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[100] animate-toast-in">
          <div className={`flex items-center gap-2.5 px-5 py-3 rounded-xl shadow-2xl border backdrop-blur-md text-sm
            ${toast.type === "success" ? "bg-green-500/10 border-green-500/20 text-green-300" :
              toast.type === "error"   ? "bg-red-500/10 border-red-500/20 text-red-300" :
                                         "bg-sky-500/10 border-sky-500/20 text-sky-300"}`}>
            <i className={`fas text-xs ${
              toast.type === "success" ? "fa-circle-check" :
              toast.type === "error"   ? "fa-circle-xmark" : "fa-circle-info"
            }`} />
            <span>{toast.msg}</span>
            <button onClick={() => setToast(null)} className="ml-2 text-ink-500 hover:text-ink-200 text-xs">✕</button>
          </div>
        </div>
      )}
      <div className="px-5 py-3 border-b border-ink-800 grad-head flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-[14px] font-semibold text-white">自选股</h2>
          <span className="text-[11px] text-ink-500">共 {items.length} 只</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="px-2.5 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-300 hover:border-ink-600"
            onClick={load} disabled={loading} title="刷新行情"
          >
            <i className={`fas fa-rotate ${loading ? "fa-spin" : ""} mr-1`} />刷新
          </button>
          <button
            className="px-2.5 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-300 hover:border-ink-600 disabled:opacity-50"
            onClick={handleScanPatterns} disabled={scanning || items.length === 0}
            title="对当前自选股运行所有形态识别器"
          >
            <i className={`fas ${scanning ? "fa-circle-notch fa-spin" : "fa-wave-square"} mr-1`} />
            {scanning ? "识别中..." : "形态识别"}
          </button>
          <button
            className="px-2.5 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-300 hover:border-ink-600"
            onClick={() => setShowOcr(true)} title="截图识别股票代码并批量导入"
          >
            <i className="fas fa-image mr-1" />截图导入
          </button>
          <div ref={addRef} className="relative">
            <button className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold"
                    onClick={() => setShowAdd(!showAdd)}>
              <i className="fas fa-plus mr-1" />添加自选
            </button>
            {showAdd && (
              <div className="absolute right-0 top-full mt-2 w-72 bg-ink-850 border border-ink-700 rounded-lg shadow-xl z-50 p-3">
                <input autoFocus value={addQuery} onChange={(e) => setAddQuery(e.target.value)}
                       placeholder="输入代码 / 名称"
                       className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-[12px] focus:outline-none focus:border-gold/60 placeholder:text-ink-500" />
                {addResults.length > 0 && (
                  <div className="mt-2 max-h-[240px] overflow-y-auto scrollbar">
                    {addResults.map((s) => {
                      const already = existingCodes.has(s.code);
                      return (
                        <button key={s.code} disabled={already || adding === s.code}
                                onClick={() => handleAdd(s.code, s.name)}
                                className="w-full text-left px-3 py-2 hover:bg-ink-800 disabled:opacity-40 flex justify-between items-baseline text-[12px] border-b border-ink-800/50 last:border-0 rounded">
                          <div>
                            <span className="text-ink-100">{s.name}</span>
                            <span className="text-ink-500 num ml-2">{s.code}</span>
                          </div>
                          <span className="text-[10px]">
                            {already ? <span className="text-ink-500">已添加</span>
                              : adding === s.code ? <i className="fas fa-circle-notch fa-spin text-gold" />
                              : <span className="text-gold">+ 添加</span>}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
                {addQuery.trim() && addResults.length === 0 && (
                  <div className="text-[11px] text-ink-500 text-center py-3">无匹配结果</div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {patternStats.length > 0 && (
        <div className="px-5 py-2 border-b border-ink-800 bg-ink-900/50 flex items-center gap-2 flex-wrap">
          <span className="text-[10px] text-ink-500 uppercase tracking-wider mr-1">形态筛选</span>
          <button
            onClick={clearPatternFilter}
            className={`px-2 py-0.5 text-[11px] rounded-full border transition ${
              patternFilter.size === 0
                ? "border-gold/60 text-gold bg-gold/10"
                : "border-ink-700 text-ink-400 hover:border-ink-600"
            }`}
          >全部 <span className="text-ink-600 ml-0.5">{items.length}</span></button>
          {patternStats.map(([key, info]) => {
            const active = patternFilter.has(key);
            return (
              <button key={key} onClick={() => togglePattern(key)}
                className={`px-2 py-0.5 text-[11px] rounded-full border transition ${
                  active ? "border-gold/60 text-gold bg-gold/10" : "border-ink-700 text-ink-300 hover:border-ink-600"
                }`}>
                {info.label}<span className="text-ink-600 ml-1">{info.count}</span>
              </button>
            );
          })}
          {noPatternCount > 0 && (
            <span className="text-[10px] text-ink-600 ml-auto">未识别形态 {noPatternCount} 只</span>
          )}
        </div>
      )}

      <div className="overflow-auto scrollbar flex-1">
        {items.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-ink-500">
            <i className="far fa-star text-3xl mb-3 text-ink-600" />
            <div className="text-[13px]">暂无自选股</div>
            <div className="text-[11px] mt-1">点击右上角「添加自选」开始跟踪</div>
          </div>
        ) : (
          <table className="w-full text-[12px] num">
            <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
              <tr className="border-b border-ink-800">
                <th className="text-left font-normal px-5 py-2.5 w-8">#</th>
                {COLUMNS.map((c) => {
                  const active = sortKey === c.key;
                  return (
                    <th key={c.key}
                        className={`font-normal px-2 cursor-pointer select-none hover:text-ink-200 transition whitespace-nowrap text-${c.align} ${active ? "text-gold" : ""}`}
                        onClick={() => handleSort(c.key)}>
                      {c.label}
                      {active && <i className={`fas fa-caret-${sortDir === "desc" ? "down" : "up"} ml-1 text-[9px]`} />}
                    </th>
                  );
                })}
                <th className="text-left font-normal px-2">备注</th>
                <th className="text-right font-normal px-5"></th>
              </tr>
            </thead>
            <tbody className="text-ink-200">
              {loading && items.length === 0 && (
                <tr>
                  <td colSpan={COLUMNS.length + 3} className="text-center text-ink-500 py-10">
                    <i className="fas fa-circle-notch fa-spin mr-2" />加载中...
                  </td>
                </tr>
              )}
              {sorted.map((r, i) => {
                const up = r.change_pct >= 0;
                const hasPrice = r.price > 0;
                return (
                  <tr key={r.code} onClick={() => onPickStock(r.code)}
                      className="row-hover border-b border-ink-850/60 cursor-pointer">
                    <td className="px-5 py-2.5 text-ink-500">{i + 1}</td>
                    <td className="px-2">
                      <div className="font-sans text-ink-100">{r.name}</div>
                      <div className="text-[10px] text-ink-500 flex items-center gap-1">
                        <span>{r.code}</span>
                        {r.market && <span className="text-ink-600">·{r.market}</span>}
                      </div>
                    </td>
                    <td className="px-2 text-ink-400 truncate max-w-[100px]" title={r.industry}>{r.industry || "—"}</td>
                    <td className={"text-right px-2 " + (hasPrice ? (up ? "text-cn-up" : "text-cn-dn") : "text-ink-500")}>
                      {hasPrice ? r.price.toFixed(2) : "—"}
                    </td>
                    <td className={"text-right px-2 " + (hasPrice ? (up ? "text-cn-up" : "text-cn-dn") : "text-ink-500")}>
                      {hasPrice ? `${up ? "+" : ""}${r.change_pct.toFixed(2)}%` : "—"}
                    </td>
                    <td className="text-right px-2 text-ink-300">{fmtAmount(r.amount)}</td>
                    <td className="text-right px-2 text-ink-300">{r.turnover_rate ? r.turnover_rate.toFixed(2) + "%" : "—"}</td>
                    <td className="text-right px-2 text-ink-300">{r.pe != null && r.pe > 0 ? r.pe.toFixed(1) : "—"}</td>
                    <td className="text-right px-2 text-ink-300">{fmtMcap(r.market_cap)}</td>
                    <td className={"text-right px-2 " + (r.roe != null && r.roe >= 10 ? "text-green-400" : "text-ink-300")}>
                      {r.roe != null ? r.roe.toFixed(1) + "%" : "—"}
                    </td>
                    <td className="px-2">
                      <span className={`text-[10px] font-semibold ${FUND_COLOR[r.fundamental_status] ?? "text-ink-600"}`}
                            title={r.fundamental_summary || ""}>
                        {FUND_LABEL[r.fundamental_status] ?? "—"}
                      </span>
                    </td>
                    <td className="px-2">
                      {r.pattern_label ? (
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-gold/15 text-gold inline-block w-fit"
                                title={r.triggers?.length ? r.triggers.join('\n') : r.pattern_label}>
                            <i className="fas fa-bookmark text-[9px] mr-1" />{r.pattern_label}
                          </span>
                          {r.triggers?.length > 0 && (
                            <span className="text-[9px] text-ink-400 leading-tight max-w-[180px] truncate" title={r.triggers.join('\n')}>
                              {r.triggers.slice(0, 2).join('；')}
                            </span>
                          )}
                        </div>
                      ) : <span className="text-ink-700">—</span>}
                    </td>
                    <td className="px-2">
                      {r.signal && r.signal !== "neutral" ? (
                        <div className="flex flex-col gap-0.5">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded inline-block w-fit font-medium ${SIGNAL_COLOR[r.signal] || "text-ink-600"}`}
                                title={r.signal_reason || r.signal_label}>
                            {SIGNAL_ICON[r.signal] && <i className={`fas ${SIGNAL_ICON[r.signal]} text-[9px] mr-1`} />}
                            {r.signal_label}
                          </span>
                          {r.signal_reason && (
                            <span className="text-[9px] text-ink-400 leading-tight max-w-[200px] truncate" title={r.signal_reason}>
                              {r.signal_reason}
                            </span>
                          )}
                        </div>
                      ) : <span className="text-ink-700">—</span>}
                    </td>
                    <td className="text-right px-2">
                      {r.score != null ? (
                        <span className={r.score >= 80 ? "text-gold font-medium" : r.score >= 60 ? "text-sky2" : "text-ink-400"}>
                          {Math.round(r.score)}
                        </span>
                      ) : <span className="text-ink-700">—</span>}
                    </td>
                    <td className="text-right px-2">
                      {r.rr_ratio != null && r.rr_ratio > 0 ? (
                        <span className={r.rr_ratio >= 2 ? "text-green-400" : r.rr_ratio >= 1 ? "text-ink-300" : "text-red-400"}>
                          {r.rr_ratio.toFixed(2)}
                        </span>
                      ) : <span className="text-ink-700">—</span>}
                    </td>
                    <td className="text-right px-2">
                      {r.distance_to_support_pct != null ? (
                        <span className={r.distance_to_support_pct <= 3 ? "text-amber-400" : "text-ink-400"}>
                          {(r.distance_to_support_pct >= 0 ? "+" : "") + r.distance_to_support_pct.toFixed(2) + "%"}
                        </span>
                      ) : <span className="text-ink-700">—</span>}
                    </td>
                    <td className="px-2 text-ink-500 text-[11px] font-sans max-w-[120px] truncate" title={r.note}>
                      {r.note || "—"}
                    </td>
                    <td className="text-right pr-5">
                      <button className="text-ink-500 hover:text-cn-dn"
                              onClick={(e) => handleRemove(e, r.code)} title="移除">
                        <i className="far fa-trash-can" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {showOcr && (
        <div className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4"
             onClick={(e) => { if (e.target === e.currentTarget) closeOcr(); }}>
          <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-3xl max-h-[88vh] flex flex-col">
            <div className="px-5 py-3 border-b border-ink-800 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <i className="fas fa-image text-gold" />
                <span className="text-[13px] font-semibold text-white">截图导入自选</span>
                <span className="text-[10px] text-ink-500">同花顺 / 东财 / 雪球 截图均可</span>
              </div>
              <button onClick={closeOcr} className="text-ink-500 hover:text-ink-200 text-[14px]">
                <i className="fas fa-xmark" />
              </button>
            </div>

            <div className="flex-1 overflow-auto scrollbar p-5 space-y-4">
              <div className="flex items-center gap-3 text-[12px]">
                <button
                  onClick={() => ocrFileRef.current?.click()}
                  disabled={ocrLoading}
                  className="px-3 py-1.5 rounded-md grad-gold text-ink-950 font-semibold disabled:opacity-50"
                >
                  <i className="fas fa-upload mr-1" />选择图片
                </button>
                <span className="text-ink-500">或</span>
                <span className="text-ink-400">在窗口内按 <kbd className="px-1.5 py-0.5 rounded bg-ink-800 border border-ink-700 text-[10px]">Ctrl/⌘ + V</kbd> 粘贴截图</span>
                <input ref={ocrFileRef} type="file" accept="image/*" className="hidden"
                       onChange={(e) => { const f = e.target.files?.[0]; if (f) processOcrFile(f); e.target.value = ""; }} />
              </div>

              {ocrLoading && (
                <div className="text-[12px] text-ink-400"><i className="fas fa-circle-notch fa-spin mr-2 text-gold" />正在识别...（首次加载 OCR 模型可能需要几秒）</div>
              )}
              {ocrError && (
                <div className="text-[12px] text-cn-dn"><i className="fas fa-triangle-exclamation mr-1" />{ocrError}</div>
              )}

              {ocrPreview && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-[11px] text-ink-500 mb-1">截图预览</div>
                    <img src={ocrPreview} alt="preview"
                         className="max-h-[420px] w-full object-contain border border-ink-800 rounded" />
                  </div>
                  <div>
                    <div className="text-[11px] text-ink-500 mb-1 flex items-center justify-between">
                      <span>识别结果（{ocrCandidates.length}）</span>
                      {ocrCandidates.length > 0 && (
                        <div className="flex gap-2 text-ink-400">
                          <button className="hover:text-gold"
                                  onClick={() => setOcrSelected(new Set(ocrCandidates.filter((c) => c.valid && !c.in_watchlist).map((c) => c.code)))}>全选可导入</button>
                          <button className="hover:text-gold" onClick={() => setOcrSelected(new Set())}>清空</button>
                        </div>
                      )}
                    </div>
                    <div className="border border-ink-800 rounded max-h-[420px] overflow-auto scrollbar">
                      {ocrCandidates.length === 0 && !ocrLoading && (
                        <div className="text-[11px] text-ink-600 text-center py-8">暂无结果</div>
                      )}
                      {ocrCandidates.map((c) => {
                        const disabled = !c.valid || c.in_watchlist;
                        const checked = ocrSelected.has(c.code);
                        return (
                          <label key={c.code}
                                 className={`flex items-center gap-2 px-3 py-2 text-[12px] border-b border-ink-800/60 last:border-0 ${
                                   disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer hover:bg-ink-850"
                                 }`}>
                            <input type="checkbox" disabled={disabled} checked={checked}
                                   onChange={() => toggleOcrPick(c.code)}
                                   className="accent-gold" />
                            <span className="num text-ink-200 w-16">{c.code}</span>
                            <span className="flex-1 text-ink-300 truncate">{c.name || "—"}</span>
                            <span className="text-[10px] text-ink-600">{(c.confidence * 100).toFixed(0)}%</span>
                            {c.in_watchlist && <span className="text-[10px] text-ink-500">已在自选</span>}
                            {!c.valid && <span className="text-[10px] text-cn-dn">未知代码</span>}
                          </label>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="px-5 py-3 border-t border-ink-800 flex items-center justify-between">
              <span className="text-[11px] text-ink-500">已选 <span className="text-gold">{ocrSelected.size}</span> 只</span>
              <div className="flex gap-2">
                <button onClick={closeOcr}
                        className="px-3 py-1.5 text-[12px] rounded-md border border-ink-700 text-ink-300 hover:border-ink-600">取消</button>
                <button onClick={handleImportSelected}
                        disabled={importing || ocrSelected.size === 0}
                        className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold disabled:opacity-50">
                  {importing ? <><i className="fas fa-circle-notch fa-spin mr-1" />导入中</> : <><i className="fas fa-plus mr-1" />导入选中</>}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
