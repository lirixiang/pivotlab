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

function SortBtn({ k, label, cur, dir, onClick }: {
  k: SortKey; label: string; cur: SortKey; dir: SortDir;
  onClick: (k: SortKey) => void;
}) {
  const active = cur === k;
  return (
    <button
      onClick={() => onClick(k)}
      className={
        "px-1 py-0.5 rounded text-[10px] tracking-wide transition cursor-pointer " +
        (active ? "text-ink-200" : "text-ink-500 hover:text-ink-300")
      }
    >
      {label}
      {active && k !== "default" && (
        <i className={"fas fa-caret-" + (dir === "desc" ? "down" : "up") + " ml-0.5 text-[9px]"} />
      )}
    </button>
  );
}

export function WatchlistPanel({
  activeCode,
  onSelect,

  refreshKey,
}: {
  activeCode: string;
  onSelect: (code: string) => void;

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
          va = a.change_pct ?? 0;
          vb = b.change_pct ?? 0;
          break;
        case "price":
          va = a.price ?? 0;
          vb = b.price ?? 0;
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
      <div className="p-3 pb-0 border-b border-ink-800">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="tag text-ink-500">自选 · WATCHLIST</span>
            <span className="text-[11px] text-ink-500">{items.length}</span>
          </div>
        </div>
        {/* Column headers with sort */}
        <div className="flex items-center justify-between px-0 pb-2 text-[10px]">
          <SortBtn k="default" label="名称" cur={sortKey} dir={sortDir} onClick={handleSort} />
          <div className="flex items-center gap-3">
            <div className="w-[40px] text-center">
              <SortBtn k="decision" label="决策分" cur={sortKey} dir={sortDir} onClick={handleSort} />
            </div>
            <div className="w-[50px] text-right">
              <SortBtn k="price" label="现价" cur={sortKey} dir={sortDir} onClick={handleSort} />
            </div>
            <div className="w-[52px] text-right">
              <SortBtn k="change" label="涨跌" cur={sortKey} dir={sortDir} onClick={handleSort} />
            </div>
          </div>
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
          const price = it.price ?? 0;
          const changePct = it.change_pct ?? 0;
          const up = changePct >= 0;
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
                  <div className="w-[40px] text-center" title="决策分">
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
                  <div className="w-[50px] text-right num">
                    {price > 0 ? (
                      <div className={"text-[13px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                        {price.toFixed(2)}
                      </div>
                    ) : (
                      <div className="text-[11px] text-ink-600">—</div>
                    )}
                  </div>
                  {/* Change */}
                  <div className="w-[52px] text-right num">
                    {price > 0 ? (
                      <div className={"text-[13px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                        {up ? "+" : ""}{changePct.toFixed(2)}%
                      </div>
                    ) : (
                      <div className="text-[11px] text-ink-600">—</div>
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

    </aside>
  );
}
