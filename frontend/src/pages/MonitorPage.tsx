import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { WatchlistItem } from "../types";

type SortKey =
  | "name" | "industry" | "price" | "change_pct" | "amount"
  | "turnover_rate" | "pe" | "market_cap" | "roe"
  | "score" | "rr_ratio" | "distance_to_support_pct"
  | "fundamental" | "pattern";
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

  const [showAdd, setShowAdd] = useState(false);
  const [addQuery, setAddQuery] = useState("");
  const [addResults, setAddResults] = useState<{ code: string; name: string; industry: string }[]>([]);
  const [adding, setAdding] = useState("");
  const addRef = useRef<HTMLDivElement>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

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
        case "score":         return r.score ?? -Infinity;
        case "rr_ratio":      return r.rr_ratio ?? -Infinity;
        case "distance_to_support_pct": return r.distance_to_support_pct ?? Number.POSITIVE_INFINITY;
      }
    };
    return [...items].sort((a, b) => {
      const va = getVal(a), vb = getVal(b);
      if (typeof va === "string" && typeof vb === "string") return va.localeCompare(vb) * mul;
      return ((va as number) - (vb as number)) * mul;
    });
  }, [items, sortKey, sortDir]);

  const existingCodes = new Set(items.map((i) => i.code));

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
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
                <th className="text-center font-normal px-2 w-16">走势</th>
                <th className="text-left font-normal px-2">备注</th>
                <th className="text-right font-normal px-5"></th>
              </tr>
            </thead>
            <tbody className="text-ink-200">
              {loading && items.length === 0 && (
                <tr>
                  <td colSpan={COLUMNS.length + 4} className="text-center text-ink-500 py-10">
                    <i className="fas fa-circle-notch fa-spin mr-2" />加载中...
                  </td>
                </tr>
              )}
              {sorted.map((r, i) => {
                const up = r.change_pct >= 0;
                const hasPrice = r.price > 0;
                const sparkUp = r.sparkline.length >= 2 ? r.sparkline[r.sparkline.length - 1] >= r.sparkline[0] : up;
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
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-gold/15 text-gold" title={`已入选: ${r.pattern_label}`}>
                          <i className="fas fa-bookmark text-[9px] mr-1" />{r.pattern_label}
                        </span>
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
                    <td className="text-center px-2">
                      <Sparkline data={r.sparkline} up={sparkUp} />
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
    </div>
  );
}
