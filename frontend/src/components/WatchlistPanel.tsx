import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { WatchlistItem, WatchlistScore } from "../types";

type SortKey = "default" | "decision" | "change" | "price";
type SortDir = "asc" | "desc";

const SORT_OPTIONS: { key: SortKey; label: string; defaultDir: SortDir }[] = [
  { key: "default", label: "默认", defaultDir: "desc" },
  { key: "decision", label: "决策分", defaultDir: "desc" },
  { key: "change", label: "涨跌", defaultDir: "desc" },
  { key: "price", label: "现价", defaultDir: "desc" },
];

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
  const [scores, setScores] = useState<Record<string, WatchlistScore>>({});
  const [sortKey, setSortKey] = useState<SortKey>("default");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Load watchlist from DB
  const loadWatchlist = useCallback(() => {
    api.watchlist().then(setItems).catch(() => {});
  }, []);

  // Load decision scores
  const loadScores = useCallback(() => {
    api.watchlistScores().then((list) => {
      const map: Record<string, WatchlistScore> = {};
      for (const s of list) map[s.code] = s;
      setScores(map);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    loadWatchlist();
    loadScores();
  }, [loadWatchlist, loadScores, refreshKey]);

  // Auto-refresh watchlist every 30s, scores every 5min
  useEffect(() => {
    const t1 = setInterval(loadWatchlist, 30_000);
    const t2 = setInterval(loadScores, 300_000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [loadWatchlist, loadScores]);

  const handleRemove = async (code: string) => {
    await api.removeWatch(code);
    setItems((prev) => prev.filter((i) => i.code !== code));
  };

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      const opt = SORT_OPTIONS.find((o) => o.key === key)!;
      setSortKey(key);
      setSortDir(opt.defaultDir);
    }
  };

  const sortedItems = useMemo(() => {
    if (sortKey === "default") return items;
    const arr = [...items];
    const mul = sortDir === "desc" ? -1 : 1;
    arr.sort((a, b) => {
      let va: number, vb: number;
      switch (sortKey) {
        case "decision":
          va = scores[a.code]?.decision_score ?? -1;
          vb = scores[b.code]?.decision_score ?? -1;
          break;
        case "change":
          va = a.change_pct;
          vb = b.change_pct;
          break;
        case "price":
          va = a.price;
          vb = b.price;
          break;
        default:
          return 0;
      }
      return (va - vb) * mul;
    });
    return arr;
  }, [items, scores, sortKey, sortDir]);



  return (
    <aside className="border-r border-ink-700 bg-ink-900 flex flex-col h-full">
      <div className="p-3 border-b border-ink-800">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="tag text-ink-500">自选 · WATCHLIST</span>
            <span className="text-[11px] text-ink-500">{items.length}</span>
          </div>
        </div>
        {/* Sort bar */}
        <div className="flex items-center gap-1">
          {SORT_OPTIONS.map((opt) => {
            const active = sortKey === opt.key;
            return (
              <button
                key={opt.key}
                onClick={() => handleSort(opt.key)}
                className={
                  "px-2 py-0.5 rounded text-[10px] transition " +
                  (active
                    ? "bg-ink-750 text-white"
                    : "text-ink-500 hover:text-ink-300")
                }
              >
                {opt.label}
                {active && sortKey !== "default" && (
                  <i className={"fas fa-caret-" + (sortDir === "desc" ? "down" : "up") + " ml-0.5 text-[9px]"} />
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="overflow-y-auto scrollbar flex-1">
        {items.length === 0 && (
          <div className="p-6 text-center text-ink-500 text-[12px]">
            <i className="fas fa-star text-2xl text-ink-700 mb-3 block" />
            <div>还没有自选股</div>
            <div className="mt-1 text-[11px]">在K线图点 ☆ 添加自选</div>
          </div>
        )}
        {sortedItems.map((it) => {
          const active = it.code === activeCode;
          const up = it.change_pct >= 0;
          const sc = scores[it.code];
          const dscore = sc?.decision_score ?? null;
          const dlabel = sc?.decision_label ?? "";
          const scoreColor =
            dscore === null ? "text-ink-600"
            : dscore >= 80 ? "text-cn-up"
            : dscore >= 60 ? "text-green-400"
            : dscore >= 40 ? "text-ink-400"
            : dscore >= 20 ? "text-orange-400"
            : "text-cn-dn";
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
                <div className="flex items-baseline gap-3">
                  {/* Decision score */}
                  <div className="text-center" title="决策分">
                    {dscore !== null ? (
                      <>
                        <div className={"text-[13px] font-medium num " + scoreColor}>{dscore}</div>
                        <div className={"text-[9px] " + scoreColor}>{dlabel}</div>
                      </>
                    ) : (
                      <div className="text-[10px] text-ink-700">···</div>
                    )}
                  </div>
                  {/* Price */}
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
