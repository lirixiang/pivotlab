import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type AiScanHit } from "../services/api";
import { useUrlParam } from "../utils/useUrlParam";
import type { AiModelStatus } from "../types";

// ── Constants ──
const MODEL_LABELS: Record<string, string> = {
  lightgbm: "LightGBM", transformer: "Transformer", lstm: "LSTM",
  cnn_lstm: "CNN-LSTM", rl_ppo: "RL-PPO",
};

const MODEL_COLORS: Record<string, { bg: string; text: string }> = {
  lightgbm:    { bg: "bg-green-900/40",  text: "text-green-400"  },
  transformer: { bg: "bg-purple-900/40", text: "text-purple-400" },
  lstm:        { bg: "bg-blue-900/40",   text: "text-blue-400"   },
  cnn_lstm:    { bg: "bg-amber-900/40",  text: "text-amber-400"  },
  rl_ppo:      { bg: "bg-cyan-900/40",   text: "text-cyan-400"   },
};

type SortKey = "rating" | "confidence" | "agreement" | "rr" | "change_pct" | "amount" | "current_price";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; defaultDir: SortDir }[] = [
  { key: "rating",        label: "评级",     defaultDir: "desc" },
  { key: "confidence",    label: "置信度",   defaultDir: "desc" },
  { key: "agreement",     label: "一致性",   defaultDir: "desc" },
  { key: "current_price", label: "现价",     defaultDir: "desc" },
  { key: "change_pct",    label: "涨跌幅",   defaultDir: "desc" },
  { key: "amount",        label: "成交额",   defaultDir: "desc" },
  { key: "rr",            label: "盈亏比",   defaultDir: "desc" },
];

// ── Inline sparkline SVG ──
function Sparkline({ data, up, width = 60, height = 22 }: { data: number[]; up: boolean; width?: number; height?: number }) {
  if (!data || data.length < 2) return <span className="text-ink-700">—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const pts = data.map((v, i) => `${(i * stepX).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`).join(" ");
  const color = up ? "#22c55e" : "#ef4444";
  return (
    <svg width={width} height={height} className="inline-block align-middle">
      <polyline fill="none" stroke={color} strokeWidth="1.2" points={pts} />
    </svg>
  );
}

// ── Star rating ──
function StarRating({ value }: { value: number }) {
  // value 0-5
  const v = Math.max(0, Math.min(5, value));
  const full = Math.floor(v);
  const hasHalf = v - full >= 0.5;
  const empty = 5 - full - (hasHalf ? 1 : 0);
  return (
    <span className="inline-flex items-center gap-0.5 text-[11px] leading-none">
      {Array.from({ length: full }).map((_, i) => <i key={`f${i}`} className="fas fa-star text-amber-400" />)}
      {hasHalf && <i className="fas fa-star-half-alt text-amber-400" />}
      {Array.from({ length: empty }).map((_, i) => <i key={`e${i}`} className="far fa-star text-ink-700" />)}
      <span className="num text-ink-300 ml-1.5">{v.toFixed(1)}</span>
    </span>
  );
}

// ── Sortable header cell ──
function SortTh({
  k, label, sortKey, sortDir, onClick, align = "right", className = "",
}: { k: SortKey; label: string; sortKey: SortKey; sortDir: SortDir; onClick: (k: SortKey) => void; align?: "left" | "right"; className?: string }) {
  const active = sortKey === k;
  return (
    <th
      className={`text-${align} font-normal px-2 cursor-pointer select-none hover:text-ink-200 transition whitespace-nowrap ${active ? "text-gold" : ""} ${className}`}
      onClick={() => onClick(k)}
    >
      {label}
      {active && <i className={`fas fa-caret-${sortDir === "desc" ? "down" : "up"} ml-1 text-[9px]`} />}
    </th>
  );
}

// ── Format helpers ──
const fmtAmount = (v: number | null | undefined) => {
  if (v == null) return "—";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return v.toFixed(0);
};

export function AIScanPage({ defaultCode, onPickStock }: { defaultCode: string; onPickStock?: (c: string) => void }) {
  const [scope, setScope] = useUrlParam<"watchlist" | "industry" | "cached">("scope", "cached");
  const [scopeCode, setScopeCode] = useState(defaultCode);
  const [industryInfo, setIndustryInfo] = useState<{ industry: string; stocks: { code: string; name: string }[] } | null>(null);
  const [status, setStatus] = useState<AiModelStatus | null>(null);
  const [useAllModels, setUseAllModels] = useState(true);
  const [selectedModel, setSelectedModel] = useState<string>("lightgbm");
  const [threshold, setThreshold] = useState(() => Number(localStorage.getItem("aiscan_thresh") || 0.4));
  const [minConfidence, setMinConfidence] = useState(() => Number(localStorage.getItem("aiscan_minConf") || 0));
  const [actionFilter, setActionFilter] = useUrlParam<"all" | "buy" | "sell">("act", "all");
  const [tasks, setTasks] = useState<Array<{
    task_id: string; scope: string; status: string; progress: number;
    message: string; total: number; scanned: number;
    started_at: number; ended_at: number | null; results: AiScanHit[];
  }>>([]);
  const [snapshotResults, setSnapshotResults] = useState<AiScanHit[] | null>(null);
  const [activeSnap, setActiveSnap] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<{ ts: string; scope: string; hits_total: number; scanned: number; ended_at: number | null }>>([]);
  const [histOpen, setHistOpen] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [sortKey, setSortKey] = useUrlParam<SortKey>("sort", "rating");
  const [sortDir, setSortDir] = useUrlParam<SortDir>("dir", "desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(() => Number(localStorage.getItem("aiscan_pageSize") || 50));
  const histRef = useRef<HTMLDivElement>(null);

  // Persist (URL state handled by useUrlParam; only true preferences saved here)
  useEffect(() => { localStorage.setItem("aiscan_thresh", String(threshold)); }, [threshold]);
  useEffect(() => { localStorage.setItem("aiscan_minConf", String(minConfidence)); }, [minConfidence]);

  // Load model status
  useEffect(() => { api.aiStatus().then(setStatus).catch(() => {}); }, []);

  // Poll scan tasks
  useEffect(() => {
    const poll = () => api.aiScanProgress().then(setTasks).catch(() => {});
    poll();
    const iv = setInterval(poll, 2000);
    return () => clearInterval(iv);
  }, []);

  // Load history
  const fetchHistory = useCallback(() => {
    api.aiScanHistory().then(setHistory).catch(() => {});
  }, []);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Industry info
  useEffect(() => {
    if (scope === "industry" && scopeCode) {
      api.aiIndustryStocks(scopeCode).then(setIndustryInfo).catch(() => setIndustryInfo(null));
    }
  }, [scope, scopeCode]);

  // Close history dropdown on outside click
  useEffect(() => {
    if (!histOpen) return;
    const h = (e: MouseEvent) => {
      if (histRef.current && !histRef.current.contains(e.target as Node)) setHistOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [histOpen]);

  const trainedModels = status
    ? (Object.entries(status) as [string, { trained: boolean }][])
        .filter(([, v]) => v.trained).map(([k]) => k)
    : [];

  const activeTask = tasks.find((t) => ["pending", "loading", "scanning"].includes(t.status));
  const latestDone = tasks.find((t) => t.status === "completed");
  const liveResults = activeTask?.results ?? latestDone?.results ?? [];
  const rawResults: AiScanHit[] = activeSnap ? (snapshotResults ?? []) : liveResults;
  // Defensive normalisation — guard against backend/snapshot drift
  const results: AiScanHit[] = useMemo(() => (rawResults ?? []).map((r) => ({
    ...r,
    sparkline: Array.isArray(r.sparkline) ? r.sparkline : [],
    concepts: Array.isArray(r.concepts) ? r.concepts : [],
    model_hits: Array.isArray(r.model_hits) ? r.model_hits : [],
    triggers: Array.isArray(r.triggers) ? r.triggers : [],
    rating: Number(r.rating) || 0,
    confidence: Number(r.confidence) || 0,
    agreement: Number(r.agreement) || 0,
    risk_reward: Number(r.risk_reward) || 0,
    current_price: Number(r.current_price) || 0,
    entry_price: Number(r.entry_price) || 0,
    stop_loss: Number(r.stop_loss) || 0,
    target_price: Number(r.target_price) || 0,
    models_agree: Number(r.models_agree) || 0,
    models_total: Number(r.models_total) || 0,
  })), [rawResults]);

  const startScan = () => {
    const modelTypes = useAllModels ? trainedModels : [selectedModel];
    if (!modelTypes.length) return;
    setLaunching(true);
    setActiveSnap(null);
    setSnapshotResults(null);
    api.aiScan({
      scope, scope_code: scopeCode, model_types: modelTypes,
      buy_threshold: threshold, sell_threshold: threshold,
    })
      .then(() => api.aiScanProgress().then(setTasks))
      .catch(() => {})
      .finally(() => setLaunching(false));
  };

  const cancelScan = () => {
    if (activeTask) {
      api.aiScanCancel(activeTask.task_id).then(() => api.aiScanProgress().then(setTasks));
    }
  };

  const loadSnapshot = async (ts: string) => {
    setActiveSnap(ts);
    setHistOpen(false);
    try {
      const snap = await api.aiScanSnapshot(ts);
      if (snap.error) setSnapshotResults([]); else setSnapshotResults(snap.results);
    } catch {
      setSnapshotResults([]);
    }
  };

  const loadLatest = () => {
    setActiveSnap(null);
    setSnapshotResults(null);
    setHistOpen(false);
  };

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      const col = COLUMNS.find((c) => c.key === key)!;
      setSortKey(key);
      setSortDir(col.defaultDir);
    }
  };

  const filtered = useMemo(() => {
    let arr = results;
    if (minConfidence > 0) arr = arr.filter((r) => r.confidence >= minConfidence);
    if (actionFilter !== "all") arr = arr.filter((r) => r.action === actionFilter);
    const mul = sortDir === "desc" ? -1 : 1;
    return [...arr].sort((a, b) => {
      let va = 0, vb = 0;
      switch (sortKey) {
        case "rating":        va = a.rating; vb = b.rating; break;
        case "confidence":    va = a.confidence; vb = b.confidence; break;
        case "agreement":     va = a.agreement; vb = b.agreement; break;
        case "rr":            va = a.risk_reward; vb = b.risk_reward; break;
        case "change_pct":    va = a.change_pct ?? 0; vb = b.change_pct ?? 0; break;
        case "amount":        va = a.amount ?? 0; vb = b.amount ?? 0; break;
        case "current_price": va = a.current_price; vb = b.current_price; break;
      }
      return (va - vb) * mul;
    });
  }, [results, minConfidence, actionFilter, sortKey, sortDir]);

  const buys = filtered.filter((r) => r.action === "buy").length;
  const sells = filtered.filter((r) => r.action === "sell").length;
  const high = filtered.filter((r) => r.rating >= 4).length;

  // ── Pagination ──
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageStart = (safePage - 1) * pageSize;
  const pageItems = useMemo(
    () => filtered.slice(pageStart, pageStart + pageSize),
    [filtered, pageStart, pageSize],
  );
  // Reset to page 1 whenever the underlying list shape changes
  useEffect(() => { setPage(1); }, [results, minConfidence, actionFilter, sortKey, sortDir, pageSize]);
  useEffect(() => { localStorage.setItem("aiscan_pageSize", String(pageSize)); }, [pageSize]);

  const openStock = (code: string) => {
    if (onPickStock) onPickStock(code);
    else window.open(`/stock/${code}`, "_blank");
  };

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      {/* ── Top toolbar ── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head flex-wrap gap-y-2">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Scope */}
          <div className="seg">
            {([
              { k: "watchlist" as const, l: "自选", icon: "fa-star" },
              { k: "industry" as const,  l: "同行业", icon: "fa-industry" },
              { k: "cached" as const,    l: "全市场", icon: "fa-database" },
            ]).map((s) => (
              <button key={s.k} className={scope === s.k ? "on" : ""} onClick={() => setScope(s.k)}>
                <i className={`fas ${s.icon} mr-1 text-[9px]`} /> {s.l}
              </button>
            ))}
          </div>

          {scope === "industry" && (
            <div className="flex items-center gap-1.5">
              <input className="bg-ink-850 border border-ink-700 rounded px-2 py-1 text-[11px] num w-20"
                     value={scopeCode} onChange={(e) => setScopeCode(e.target.value)} placeholder="600519" />
              {industryInfo && (
                <span className="text-[10px] text-blue-400">
                  {industryInfo.industry} ({industryInfo.stocks.length})
                </span>
              )}
            </div>
          )}

          {/* Action filter */}
          <div className="seg">
            <button className={actionFilter === "all" ? "on" : ""} onClick={() => setActionFilter("all")}>全部</button>
            <button className={actionFilter === "buy" ? "on" : ""} onClick={() => setActionFilter("buy")}>
              <span className="text-green-400">▲</span> 买
            </button>
            <button className={actionFilter === "sell" ? "on" : ""} onClick={() => setActionFilter("sell")}>
              <span className="text-red-400">▼</span> 卖
            </button>
          </div>

          {/* Min confidence slider */}
          <div className="flex items-center gap-2 text-[11px] text-ink-400">
            <span>≥</span>
            <input type="range" min={0} max={100} step={5} value={minConfidence}
                   onChange={(e) => setMinConfidence(Number(e.target.value))}
                   className="w-20 accent-gold" />
            <span className="num text-ink-200 w-7 text-right">{minConfidence}</span>
            <span>分</span>
          </div>

          {/* Stats pills */}
          <div className="flex items-center gap-2 text-[11px]">
            <span className="chip"><span className="text-amber-400 num mr-1">{high}</span> ★4+</span>
            <span className="chip"><span className="text-green-400 num mr-1">{buys}</span> 买入</span>
            <span className="chip"><span className="text-red-400 num mr-1">{sells}</span> 卖出</span>
            <span className="chip"><span className="text-ink-100 num mr-1">{filtered.length}</span> 入选</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Threshold input */}
          <div className="flex items-center gap-1.5 text-[10px] text-ink-500">
            <span>阈值</span>
            <input type="number" className="bg-ink-850 border border-ink-700 rounded px-1.5 py-0.5 w-12 num text-[11px] text-ink-200"
                   step={0.05} min={0.1} max={0.9} value={threshold}
                   onChange={(e) => setThreshold(Number(e.target.value))} />
          </div>

          {/* Model select */}
          <button
            className={`px-2 py-1 text-[11px] rounded border transition ${useAllModels ? "border-purple-700 text-purple-300 bg-purple-900/20" : "border-ink-700 text-ink-400 hover:border-ink-600"}`}
            onClick={() => setUseAllModels(true)}
            title="融合所有已训练模型"
          >
            <i className="fas fa-layer-group mr-1 text-[9px]" />融合 ({trainedModels.length})
          </button>
          {!useAllModels && (
            <select className="bg-ink-850 border border-ink-700 rounded px-1.5 py-1 text-[11px] text-ink-200"
                    value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
              {trainedModels.map((m) => <option key={m} value={m}>{MODEL_LABELS[m] ?? m}</option>)}
            </select>
          )}
          <button
            className={`px-2 py-1 text-[11px] rounded border transition ${!useAllModels ? "border-purple-700 text-purple-300 bg-purple-900/20" : "border-ink-700 text-ink-400 hover:border-ink-600"}`}
            onClick={() => setUseAllModels(false)}
          >
            单选
          </button>

          {/* History dropdown */}
          <div className="relative" ref={histRef}>
            <button
              className="px-2 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600 transition"
              onClick={() => { if (!histOpen) fetchHistory(); setHistOpen(!histOpen); }}
            >
              <i className="fas fa-clock-rotate-left mr-1 text-[9px]" />
              {activeSnap ? `${activeSnap.slice(4,6)}-${activeSnap.slice(6,8)} ${activeSnap.slice(9,11)}:${activeSnap.slice(11)}` : "历史"}
              <i className="fas fa-chevron-down ml-1 text-[8px]" />
            </button>
            {histOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-72 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 max-h-80 overflow-y-auto scrollbar">
                <button
                  className={`w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition flex items-center gap-2 ${!activeSnap ? "text-gold" : "text-ink-300"}`}
                  onClick={loadLatest}
                >
                  <i className={`fas fa-${activeSnap ? "arrow-left" : "check"} text-[9px] w-3`} />
                  <span className="font-medium">最新结果</span>
                </button>
                <div className="border-t border-ink-700 my-1" />
                {history.length === 0 && (
                  <div className="px-3 py-4 text-[10px] text-ink-600 text-center">暂无历史扫描记录</div>
                )}
                {history.map((h) => {
                  const dt = h.ended_at ? new Date(h.ended_at * 1000) : null;
                  const dStr = dt ? `${String(dt.getMonth()+1).padStart(2,"0")}-${String(dt.getDate()).padStart(2,"0")} ${String(dt.getHours()).padStart(2,"0")}:${String(dt.getMinutes()).padStart(2,"0")}` : h.ts;
                  return (
                    <button key={h.ts}
                      className={`w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition flex items-center justify-between ${activeSnap === h.ts ? "text-gold bg-ink-800/50" : "text-ink-300"}`}
                      onClick={() => loadSnapshot(h.ts)}
                    >
                      <span>
                        {activeSnap === h.ts && <i className="fas fa-check text-[9px] mr-1.5" />}
                        {dStr}
                        <span className="text-ink-600 ml-1.5">[{h.scope}]</span>
                      </span>
                      <span className="text-ink-500 num">{h.hits_total} 只</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Scan / cancel */}
          {activeTask ? (
            <button className="px-3 py-1.5 text-[12px] rounded-md bg-red-900/40 hover:bg-red-900/60 text-red-300 border border-red-800/60"
                    onClick={cancelScan}>
              <i className="fas fa-stop mr-1" /> 取消
            </button>
          ) : (
            <button className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold disabled:opacity-50"
                    onClick={startScan} disabled={launching || trainedModels.length === 0}>
              {launching ? (
                <><i className="fas fa-circle-notch fa-spin mr-1" /> 启动中…</>
              ) : trainedModels.length === 0 ? (
                <><i className="fas fa-exclamation-triangle mr-1" /> 请先训练模型</>
              ) : (
                <><i className="fas fa-bolt mr-1" /> 扫描</>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Progress */}
      {activeTask && (
        <div className="px-5 py-2 border-b border-ink-800 bg-ink-900/50">
          <div className="flex items-center justify-between text-[11px] mb-1.5">
            <span className="text-ink-400">
              <i className="fas fa-circle-notch fa-spin mr-1.5 text-[9px] text-amber-400" />
              {activeTask.message}
            </span>
            <span className="num text-ink-300">
              {activeTask.scanned} / {activeTask.total}
              <span className="text-ink-500 ml-1.5">({activeTask.progress}%)</span>
            </span>
          </div>
          <div className="w-full h-1.5 bg-ink-800 rounded-full overflow-hidden">
            <div className="h-full rounded-full transition-all duration-700"
                 style={{ width: `${Math.max(1, activeTask.progress)}%`, background: "linear-gradient(90deg, #d4a857, #f0c674)" }} />
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
              <th className="text-left font-normal px-2">市场 / 行业</th>
              <th className="text-left font-normal px-2">概念</th>
              <th className="text-center font-normal px-2">基本面</th>
              <th className="text-center font-normal px-2">信号</th>
              <th className="text-center font-normal px-2">模型</th>
              <SortTh k="rating" label="评级" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" className="w-28" />
              <SortTh k="confidence" label="置信" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="agreement" label="一致" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="current_price" label="现价" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="change_pct" label="涨跌" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <SortTh k="amount" label="成交额" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <th className="text-right font-normal px-2">入场/止损/目标</th>
              <SortTh k="rr" label="盈亏比" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} />
              <th className="text-center font-normal px-2 w-16">走势</th>
              <th className="text-left font-normal px-2">触发</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {filtered.length === 0 && !activeTask && (
              <tr>
                <td colSpan={17} className="text-center text-ink-500 py-16">
                  <i className="fas fa-satellite-dish text-2xl text-ink-700 block mb-3" />
                  <div>{trainedModels.length === 0 ? "尚未训练任何模型，请先到 策略引擎 训练" : "暂无扫描结果"}</div>
                  <div className="mt-1 text-[11px]">
                    {trainedModels.length > 0 && '点击右上角 "扫描" 开始, 或调低置信度阈值'}
                  </div>
                </td>
              </tr>
            )}
            {filtered.length === 0 && activeTask && (
              <tr>
                <td colSpan={17} className="text-center text-ink-500 py-16">
                  <i className="fas fa-circle-notch fa-spin text-xl text-amber-400 mb-3 block" />
                  正在扫描，结果会实时显示…
                </td>
              </tr>
            )}
            {pageItems.map((r, i) => {
              const rowIndex = pageStart + i;
              const up = (r.change_pct ?? 0) >= 0;
              const isBuy = r.action === "buy";
              const sparkline = Array.isArray(r.sparkline) ? r.sparkline : [];
              const concepts = Array.isArray(r.concepts) ? r.concepts : [];
              const modelHits = Array.isArray(r.model_hits) ? r.model_hits : [];
              const triggers = Array.isArray(r.triggers) ? r.triggers : [];
              const sparkUp = sparkline.length >= 2 ? sparkline[sparkline.length - 1] >= sparkline[0] : true;
              const fundColor = {
                healthy: "text-green-400", neutral: "text-ink-300", weak: "text-amber-400",
                risk: "text-red-400", unknown: "text-ink-600",
              }[r.fundamental_status] ?? "text-ink-600";
              const fundLabel = {
                healthy: "优", neutral: "中", weak: "弱", risk: "险", unknown: "—",
              }[r.fundamental_status] ?? "—";

              return (
                <tr key={r.code}
                    className="row-hover border-b border-ink-850/70 cursor-pointer"
                    onClick={() => openStock(r.code)}>
                  <td className="px-5 py-2.5 text-ink-500">{rowIndex + 1}</td>
                  <td className="px-2">
                    <div className="font-sans text-ink-100">{r.name}</div>
                    <div className="text-[10px] text-ink-500">{r.code}</div>
                  </td>
                  <td className="px-2">
                    <div className="text-[10px] text-ink-400">{r.market || "—"}</div>
                    <div className="text-[11px] text-ink-300 truncate max-w-[80px]" title={r.industry}>{r.industry || "—"}</div>
                  </td>
                  <td className="px-2">
                    <div className="flex flex-wrap gap-0.5 max-w-[120px]">
                      {concepts.slice(0, 2).map((c) => (
                        <span key={c} className="text-[9px] px-1 py-px rounded bg-ink-850 text-ink-400 truncate max-w-[60px]" title={c}>{c}</span>
                      ))}
                      {concepts.length > 2 && <span className="text-[9px] text-ink-600">+{concepts.length - 2}</span>}
                    </div>
                  </td>
                  <td className="px-2 text-center">
                    <span className={`text-[10px] font-semibold ${fundColor}`}>{fundLabel}</span>
                    {r.pe != null && <div className="text-[9px] text-ink-600 num">PE {r.pe.toFixed(1)}</div>}
                  </td>
                  <td className="px-2 text-center">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                      isBuy ? "bg-green-900/40 text-green-400" : "bg-red-900/40 text-red-400"
                    }`}>
                      {isBuy ? "▲ 买入" : "▼ 卖出"}
                    </span>
                  </td>
                  <td className="px-2 text-center">
                    <div className="flex flex-wrap gap-0.5 justify-center max-w-[110px]">
                      {modelHits.map((h) => {
                        const mc = MODEL_COLORS[h.model] ?? { bg: "bg-ink-800", text: "text-ink-400" };
                        const dim = h.action !== r.action;
                        return (
                          <span key={h.model}
                                className={`text-[9px] px-1 py-px rounded ${mc.bg} ${mc.text} ${dim ? "opacity-30" : ""}`}
                                title={`${MODEL_LABELS[h.model] ?? h.model} · ${h.action} · ${h.confidence}% · RR ${h.rr.toFixed(2)}`}>
                            {(MODEL_LABELS[h.model] ?? h.model).slice(0, 3)}
                          </span>
                        );
                      })}
                    </div>
                  </td>
                  <td className="px-2">
                    <StarRating value={r.rating} />
                    <div className="text-[9px] text-ink-600 mt-0.5">{r.models_agree}/{r.models_total} 模型一致</div>
                  </td>
                  <td className="text-right px-2">
                    <span className={r.confidence >= 70 ? "text-amber-400 font-medium" : "text-ink-300"}>
                      {r.confidence.toFixed(1)}%
                    </span>
                  </td>
                  <td className="text-right px-2">
                    <span className={r.agreement >= 0.6 ? "text-amber-400" : "text-ink-400"}>
                      {(r.agreement * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="text-right px-2 text-ink-300">{r.current_price.toFixed(2)}</td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {r.change_pct == null ? "—" : (up ? "+" : "") + r.change_pct.toFixed(2) + "%"}
                  </td>
                  <td className="text-right px-2 text-ink-400">{fmtAmount(r.amount)}</td>
                  <td className="text-right px-2">
                    <div className="text-[10px] leading-tight">
                      <div className="text-blue-400 num">{r.entry_price.toFixed(2)}</div>
                      <div className="text-red-400 num">{r.stop_loss.toFixed(2)}</div>
                      <div className="text-green-400 num">{r.target_price.toFixed(2)}</div>
                    </div>
                  </td>
                  <td className="text-right px-2">
                    <span className={r.risk_reward >= 2 ? "text-green-400 font-medium" : r.risk_reward >= 1 ? "text-ink-300" : "text-red-400"}>
                      {r.risk_reward.toFixed(2)}
                    </span>
                  </td>
                  <td className="text-center px-2">
                    <Sparkline data={sparkline} up={sparkUp} />
                  </td>
                  <td className="px-2">
                    <div className="flex flex-wrap gap-0.5 max-w-[160px]">
                      {triggers.slice(0, 4).map((t, j) => (
                        <span key={j} className="text-[9px] px-1 py-px rounded bg-ink-850/70 text-ink-300">{t}</span>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length > 0 && (
          <div className="flex items-center justify-between gap-3 px-5 py-2 border-t border-ink-800 bg-ink-900/60 text-[11px] text-ink-400 sticky bottom-0">
            <div>
              共 <span className="text-ink-100 num">{filtered.length}</span> 条 ·
              第 <span className="text-ink-100 num">{pageStart + 1}</span>–
              <span className="text-ink-100 num">{Math.min(pageStart + pageSize, filtered.length)}</span> 条
            </div>
            <div className="flex items-center gap-2">
              <span className="text-ink-500">每页</span>
              <select
                className="bg-ink-850 ring-soft rounded px-2 py-1 text-[11px] text-ink-200"
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
              >
                {[20, 50, 100, 200, 500].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
              <button
                className="chip text-[11px] disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={safePage <= 1}
                onClick={() => setPage(1)}
                title="首页"
              ><i className="fas fa-angles-left text-[10px]" /></button>
              <button
                className="chip text-[11px] disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={safePage <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              ><i className="fas fa-angle-left text-[10px]" /> 上一页</button>
              <span className="num text-ink-200">
                {safePage} / {totalPages}
              </span>
              <button
                className="chip text-[11px] disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={safePage >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >下一页 <i className="fas fa-angle-right text-[10px]" /></button>
              <button
                className="chip text-[11px] disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={safePage >= totalPages}
                onClick={() => setPage(totalPages)}
                title="末页"
              ><i className="fas fa-angles-right text-[10px]" /></button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
