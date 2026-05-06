import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { WatchlistItem } from "../types";

type UnivItem = { code: string; name: string; industry: string };

export function WatchlistPanel({
  activeCode,
  onSelect,
  scanCounts,
  refreshKey,
}: {
  activeCode: string;
  onSelect: (code: string) => void;
  scanCounts: { breakout: number; bottom: number; high: number };
  refreshKey?: number;
}) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [addQuery, setAddQuery] = useState("");
  const [addResults, setAddResults] = useState<UnivItem[]>([]);
  const addRef = useRef<HTMLDivElement>(null);
  const addTimer = useRef<ReturnType<typeof setTimeout>>();

  // Load watchlist from DB
  const loadWatchlist = useCallback(() => {
    api.watchlist().then(setItems).catch(() => {});
  }, []);

  useEffect(() => {
    loadWatchlist();
  }, [loadWatchlist, refreshKey]);

  // Auto-refresh watchlist every 30s for price updates
  useEffect(() => {
    const t = setInterval(loadWatchlist, 30_000);
    return () => clearInterval(t);
  }, [loadWatchlist]);

  // Debounced remote search for add panel
  useEffect(() => {
    const q = addQuery.trim();
    if (!q) { setAddResults([]); return; }
    clearTimeout(addTimer.current);
    addTimer.current = setTimeout(() => {
      api.searchStocks(q, 10).then(setAddResults).catch(() => {});
    }, 250);
    return () => clearTimeout(addTimer.current);
  }, [addQuery]);

  // Close add popover on outside click
  useEffect(() => {
    if (!showAdd) return;
    const handler = (e: MouseEvent) => {
      if (addRef.current && !addRef.current.contains(e.target as Node)) setShowAdd(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAdd]);

  const watchedCodes = new Set(items.map((i) => i.code));

  const handleAdd = async (code: string, name: string) => {
    await api.addWatch(code, name);
    loadWatchlist();
    setShowAdd(false);
    setAddQuery("");
  };

  const handleRemove = async (code: string) => {
    await api.removeWatch(code);
    setItems((prev) => prev.filter((i) => i.code !== code));
  };



  return (
    <aside className="border-r border-ink-700 bg-ink-900 flex flex-col h-full">
      <div className="p-3 border-b border-ink-800">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="tag text-ink-500">自选 · WATCHLIST</span>
            <span className="text-[11px] text-ink-500">{items.length}</span>
          </div>
          <div className="relative" ref={addRef}>
            <button
              className="w-6 h-6 flex items-center justify-center rounded bg-ink-800 hover:bg-gold/20 text-ink-400 hover:text-gold text-sm transition"
              onClick={() => setShowAdd(!showAdd)}
              title="添加自选"
            >
              +
            </button>
            {showAdd && (
              <div className="absolute right-0 top-full mt-1 w-64 bg-ink-900 border border-ink-700 rounded-lg shadow-2xl z-50 overflow-hidden">
                <div className="p-2 border-b border-ink-800">
                  <input
                    autoFocus
                    className="w-full bg-ink-850 border border-ink-700 rounded px-3 py-1.5 text-[12px] placeholder:text-ink-500 focus:outline-none focus:border-gold/60"
                    placeholder="搜索代码/名称/行业"
                    value={addQuery}
                    onChange={(e) => setAddQuery(e.target.value)}
                  />
                </div>
                <div className="max-h-[240px] overflow-y-auto">
                  {addResults.map((s) => (
                    <button
                      key={s.code}
                      className="w-full flex items-center gap-2 px-3 py-2 text-left text-[12px] hover:bg-ink-800 transition"
                      onClick={() => handleAdd(s.code, s.name)}
                    >
                      <span className="num text-ink-500 w-14">{s.code}</span>
                      <span className="flex-1 text-ink-200">{s.name}</span>
                      {watchedCodes.has(s.code) ? (
                        <span className="text-[10px] text-gold">已添加</span>
                      ) : (
                        <span className="text-[10px] text-ink-500">{s.industry}</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

      </div>

      <div className="overflow-y-auto scrollbar flex-1">
        {items.length === 0 && (
          <div className="p-6 text-center text-ink-500 text-[12px]">
            <i className="fas fa-star text-2xl text-ink-700 mb-3 block" />
            <div>还没有自选股</div>
            <div className="mt-1 text-[11px]">点击右上角 + 或在K线图点 ☆加自选</div>
          </div>
        )}
        {items.map((it) => {
          const active = it.code === activeCode;
          const up = it.change_pct >= 0;
          return (
            <div
              key={it.code}
              onClick={() => onSelect(it.code)}
              className={
                "group px-3 py-2.5 cursor-pointer border-b border-ink-850/60 row-hover relative " +
                (active ? "border-l-2 border-gold bg-ink-850" : "")
              }
            >
              <div className="flex justify-between items-baseline">
                <div>
                  <div className={"text-[13px] " + (active ? "text-white font-medium" : "text-ink-200")}>
                    {it.name}
                  </div>
                  <div className="text-[10px] text-ink-500 num tracking-wider">
                    {it.code} · {it.code.startsWith("6") ? "SH" : "SZ"}
                    {it.industry ? ` · ${it.industry}` : ""}
                  </div>
                </div>
                <div className="text-right num">
                  {it.price > 0 ? (
                    <>
                      <div className={"text-[13px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                        {it.price.toFixed(2)}
                      </div>
                      <div className={"text-[10px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                        {up ? "+" : ""}{it.change_pct.toFixed(2)}%
                      </div>
                    </>
                  ) : (
                    <div className="text-[11px] text-ink-600">待同步</div>
                  )}
                </div>
              </div>
              {/* Remove button */}
              <button
                  className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 text-ink-600 hover:text-red-400 text-[10px] transition"
                  onClick={(e) => { e.stopPropagation(); handleRemove(it.code); }}
                  title="移除自选"
                >
                  <i className="fas fa-xmark" />
                </button>
            </div>
          );
        })}
      </div>

      <div className="border-t border-ink-800 p-3 grad-card">
        <div className="flex items-center justify-between mb-2">
          <span className="tag text-ink-500">今日扫描</span>
          <span className="text-[11px] text-gold">实时</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="bg-ink-850 ring-soft rounded-md py-2">
            <div className="num text-cn-up text-base">{scanCounts.breakout}</div>
            <div className="text-[10px] text-ink-500 mt-0.5">突破回踩</div>
          </div>
          <div className="bg-ink-850 ring-soft rounded-md py-2">
            <div className="num text-cn-dn text-base">{scanCounts.bottom}</div>
            <div className="text-[10px] text-ink-500 mt-0.5">下跌企稳</div>
          </div>
          <div className="bg-ink-850 ring-soft rounded-md py-2">
            <div className="num text-gold text-base">{scanCounts.high}</div>
            <div className="text-[10px] text-ink-500 mt-0.5">高强信号</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
