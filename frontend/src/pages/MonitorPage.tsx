import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { ScreenerItem, WatchlistItem } from "../types";

type Row = WatchlistItem & {
  signal?: ScreenerItem;
  signalKind?: "breakout" | "bottom" | "watch";
};

const FILTERS = [
  { k: "all", l: "全部" },
  { k: "breakout", l: "突破信号" },
  { k: "bottom", l: "企稳信号" },
  { k: "alert", l: "异动预警" },
];

export function MonitorPage({ onPickStock }: { onPickStock: (code: string) => void }) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [signals, setSignals] = useState<{ breakout: ScreenerItem[]; bottom: ScreenerItem[] }>({
    breakout: [],
    bottom: [],
  });
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);

  // Add-stock search
  const [showAdd, setShowAdd] = useState(false);
  const [addQuery, setAddQuery] = useState("");
  const [addResults, setAddResults] = useState<{ code: string; name: string; industry: string }[]>([]);
  const [adding, setAdding] = useState("");
  const addRef = useRef<HTMLDivElement>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

  const loadWatchlist = useCallback(() => {
    api.watchlist().then(setItems).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadWatchlist();
  }, [loadWatchlist]);

  // Close add-dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (addRef.current && !addRef.current.contains(e.target as Node)) {
        setShowAdd(false);
        setAddQuery("");
        setAddResults([]);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Debounced search for add-stock
  useEffect(() => {
    const q = addQuery.trim();
    if (!q) { setAddResults([]); return; }
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      api.searchStocks(q, 8).then(setAddResults).catch(() => {});
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [addQuery]);

  async function handleAdd(code: string, name: string) {
    setAdding(code);
    try {
      await api.addWatch(code, name);
      loadWatchlist();
      setAddQuery("");
      setAddResults([]);
      setShowAdd(false);
    } finally {
      setAdding("");
    }
  }

  async function handleRemove(e: React.MouseEvent, code: string) {
    e.stopPropagation();
    await api.removeWatch(code);
    setItems((prev) => prev.filter((i) => i.code !== code));
  }

  async function runScan() {
    setScanning(true);
    try {
      const codes = new Set(items.map((i) => i.code));
      const [b, s] = await Promise.all([
        api.screener("breakout_pullback", 500),
        api.screener("bottom_stabilize", 500),
      ]);
      // Only keep signals for our watchlist stocks
      setSignals({
        breakout: b.items.filter((i) => codes.has(i.code)),
        bottom: s.items.filter((i) => codes.has(i.code)),
      });
      // Also refresh watchlist to get latest quotes
      loadWatchlist();
    } finally {
      setScanning(false);
    }
  }

  const rows: Row[] = useMemo(() => {
    const bp = new Map(signals.breakout.map((i) => [i.code, i] as const));
    const bs = new Map(signals.bottom.map((i) => [i.code, i] as const));
    return items.map((w) => {
      const sig = bp.get(w.code) ?? bs.get(w.code);
      const kind = bp.has(w.code) ? "breakout" : bs.has(w.code) ? "bottom" : "watch";
      return { ...w, signal: sig, signalKind: kind as Row["signalKind"] };
    });
  }, [items, signals]);

  const filtered = rows.filter((r) => {
    if (filter === "breakout") return r.signalKind === "breakout";
    if (filter === "bottom") return r.signalKind === "bottom";
    if (filter === "alert") return Math.abs(r.change_pct) >= 3;
    return true;
  });

  const summary = {
    total: rows.length,
    breakout: rows.filter((r) => r.signalKind === "breakout").length,
    bottom: rows.filter((r) => r.signalKind === "bottom").length,
    alerts: rows.filter((r) => Math.abs(r.change_pct) >= 3).length,
  };

  const existingCodes = new Set(items.map((i) => i.code));

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      <div className="px-5 py-4 border-b border-ink-800 grad-head flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-semibold text-white">自选监控</h2>
          <div className="text-[11px] text-ink-500 mt-0.5">
            跟踪自选股行情与形态信号
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="px-3 py-1.5 text-[12px] rounded-md bg-sky-700 hover:bg-sky-600 text-white disabled:opacity-50 font-semibold"
            onClick={runScan}
            disabled={scanning || items.length === 0}
          >
            {scanning ? (
              <><i className="fas fa-circle-notch fa-spin mr-1" />扫描中...</>
            ) : (
              <><i className="fas fa-search mr-1" />扫描信号</>
            )}
          </button>
          <div ref={addRef} className="relative">
            <button
              className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold"
              onClick={() => setShowAdd(!showAdd)}
            >
              <i className="fas fa-plus mr-1" /> 添加自选
            </button>
            {showAdd && (
              <div className="absolute right-0 top-full mt-2 w-72 bg-ink-850 border border-ink-700 rounded-lg shadow-xl z-50 p-3">
                <input
                  autoFocus
                  value={addQuery}
                  onChange={(e) => setAddQuery(e.target.value)}
                  placeholder="输入代码 / 名称搜索"
                  className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-[12px] focus:outline-none focus:border-gold/60 placeholder:text-ink-500"
                />
                {addResults.length > 0 && (
                  <div className="mt-2 max-h-[240px] overflow-y-auto scrollbar">
                    {addResults.map((s) => {
                      const already = existingCodes.has(s.code);
                      return (
                        <button
                          key={s.code}
                          disabled={already || adding === s.code}
                          onClick={() => handleAdd(s.code, s.name)}
                          className="w-full text-left px-3 py-2 hover:bg-ink-800 disabled:opacity-40 flex justify-between items-baseline text-[12px] border-b border-ink-800/50 last:border-0 rounded"
                        >
                          <div>
                            <span className="text-ink-100">{s.name}</span>
                            <span className="text-ink-500 num ml-2">{s.code}</span>
                          </div>
                          <span className="text-[10px]">
                            {already ? (
                              <span className="text-ink-500">已添加</span>
                            ) : adding === s.code ? (
                              <i className="fas fa-circle-notch fa-spin text-gold" />
                            ) : (
                              <span className="text-gold">+ 添加</span>
                            )}
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

      <div className="grid grid-cols-4 gap-3 p-5 border-b border-ink-800">
        <Card label="自选总数" value={summary.total} hint="全部跟踪标的" color="text-white" />
        <Card label="突破信号" value={summary.breakout} hint="今日突破回踩触发" color="text-gold" />
        <Card label="企稳信号" value={summary.bottom} hint="低位止跌后回升" color="text-sky2" />
        <Card label="异动预警" value={summary.alerts} hint="涨跌幅 ≥ 3%" color="text-cn-up" />
      </div>

      <div className="flex items-center gap-3 px-5 py-2.5 border-b border-ink-800">
        <div className="seg">
          {FILTERS.map((f) => (
            <button
              key={f.k}
              onClick={() => setFilter(f.k)}
              className={filter === f.k ? "on" : ""}
            >
              {f.l}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-ink-500 ml-2">
          显示 {filtered.length} / {rows.length}
        </span>
      </div>

      <div className="overflow-y-auto scrollbar flex-1">
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
                <th className="text-left font-normal px-5 py-2.5">名称 / 代码</th>
                <th className="text-left font-normal px-2">行业</th>
                <th className="text-right font-normal px-2">现价</th>
                <th className="text-right font-normal px-2">涨跌</th>
                <th className="text-left font-normal px-2">备注</th>
                <th className="text-left font-normal px-2">形态</th>
                <th className="text-left font-normal px-2 w-28">信号强度</th>
                <th className="text-right font-normal px-5">操作</th>
              </tr>
            </thead>
            <tbody className="text-ink-200">
              {loading && (
                <tr>
                  <td colSpan={8} className="text-center text-ink-500 py-10">
                    <i className="fas fa-circle-notch fa-spin mr-2" /> 加载中...
                  </td>
                </tr>
              )}
              {!loading &&
                filtered.map((r) => {
                  const up = r.change_pct >= 0;
                  const hasPrice = r.price > 0;
                  const tag =
                    r.signalKind === "breakout"
                      ? { l: "突破回踩", c: "chip-on" }
                      : r.signalKind === "bottom"
                      ? { l: "下跌企稳", c: "chip-dn" }
                      : null;
                  return (
                    <tr
                      key={r.code}
                      onClick={() => onPickStock(r.code)}
                      className="row-hover border-b border-ink-850/60 cursor-pointer"
                    >
                      <td className="px-5 py-2.5">
                        <div className="font-sans text-ink-100">{r.name}</div>
                        <div className="text-[10px] text-ink-500">{r.code}</div>
                      </td>
                      <td className="px-2 text-ink-400">{r.industry}</td>
                      <td className={"text-right " + (hasPrice ? (up ? "text-cn-up" : "text-cn-dn") : "text-ink-500")}>
                        {hasPrice ? r.price.toFixed(2) : "—"}
                      </td>
                      <td className={"text-right " + (hasPrice ? (up ? "text-cn-up" : "text-cn-dn") : "text-ink-500")}>
                        {hasPrice ? `${up ? "+" : ""}${r.change_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td className="px-2 text-ink-500 text-[11px] font-sans max-w-[120px] truncate">
                        {r.note || "—"}
                      </td>
                      <td>
                        {tag ? (
                          <span className={"chip " + tag.c}>{tag.l}</span>
                        ) : (
                          <span className="text-ink-600">—</span>
                        )}
                      </td>
                      <td>
                        {r.signal ? (
                          <div className="flex items-center gap-2">
                            <div className="level-bar flex-1">
                              <div
                                className="level-fill"
                                style={{
                                  width: Math.min(100, r.signal.score) + "%",
                                  background:
                                    r.signalKind === "breakout"
                                      ? "linear-gradient(90deg,#d4a857,#f0c674)"
                                      : "linear-gradient(90deg,#7dd3fc,#bae6fd)",
                                }}
                              />
                            </div>
                            <span
                              className={
                                r.signalKind === "breakout" ? "text-gold" : "text-sky2"
                              }
                            >
                              {Math.round(r.signal.score)}
                            </span>
                          </div>
                        ) : (
                          <span className="text-ink-600">—</span>
                        )}
                      </td>
                      <td className="text-right pr-5">
                        <button
                          className="text-gold hover:underline mr-3"
                          onClick={(e) => { e.stopPropagation(); onPickStock(r.code); }}
                        >
                          查看
                        </button>
                        <button
                          className="text-ink-500 hover:text-cn-dn"
                          onClick={(e) => handleRemove(e, r.code)}
                        >
                          <i className="far fa-trash-can" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              {!loading && filtered.length === 0 && items.length > 0 && (
                <tr>
                  <td colSpan={8} className="text-center text-ink-500 py-10">
                    当前过滤条件下没有符合的自选股
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Card({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: number;
  hint: string;
  color: string;
}) {
  return (
    <div className="bg-ink-900 ring-soft rounded-lg p-4">
      <div className="text-[11px] text-ink-500">{label}</div>
      <div className={"num text-2xl mt-1 " + color}>{value}</div>
      <div className="text-[10px] text-ink-600 mt-1">{hint}</div>
    </div>
  );
}
