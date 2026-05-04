import { useEffect, useState } from "react";
import { api } from "../services/api";

type Item = { code: string; name: string; industry: string };

export function WatchlistPanel({
  activeCode,
  onSelect,
  scanCounts,
}: {
  activeCode: string;
  onSelect: (code: string) => void;
  scanCounts: { breakout: number; bottom: number; high: number };
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [tab, setTab] = useState<"all" | "breakout" | "bottom" | "watch">("all");

  useEffect(() => {
    api.universe().then(setItems).catch(() => {});
  }, []);

  const filtered = items;

  return (
    <aside className="border-r border-ink-700 bg-ink-900 flex flex-col h-full">
      <div className="p-3 border-b border-ink-800">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="tag text-ink-500">自选 · WATCHLIST</span>
            <span className="text-[11px] text-ink-500">{items.length}</span>
          </div>
          <button className="text-ink-500 hover:text-white text-xs">
            <i className="fas fa-plus" />
          </button>
        </div>
        <div className="seg w-full">
          {(["all", "breakout", "bottom", "watch"] as const).map((k) => (
            <button
              key={k}
              className={"flex-1 " + (tab === k ? "on" : "")}
              onClick={() => setTab(k)}
            >
              {k === "all" ? "全部" : k === "breakout" ? "突破" : k === "bottom" ? "企稳" : "观察"}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-y-auto scrollbar flex-1">
        {filtered.map((it) => {
          const active = it.code === activeCode;
          // Stable but "fake" badges for visual richness
          const seed = parseInt(it.code) % 5;
          const badges =
            seed === 0
              ? [{ cls: "chip-on", text: "突破回踩" }]
              : seed === 1
              ? [{ cls: "chip-up", text: "逼近压力" }]
              : seed === 2
              ? [{ cls: "chip-dn", text: "下跌企稳" }]
              : seed === 3
              ? [{ cls: "", text: "箱体震荡" }]
              : [{ cls: "", text: "支撑测试" }];
          // pseudo price
          const base = 10 + (parseInt(it.code) % 1000) / 5;
          const chg = ((parseInt(it.code) % 700) - 300) / 100;
          const up = chg >= 0;
          return (
            <div
              key={it.code}
              onClick={() => onSelect(it.code)}
              className={
                "px-3 py-2.5 cursor-pointer border-b border-ink-850/60 row-hover " +
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
                  </div>
                </div>
                <div className="text-right num">
                  <div className={"text-[13px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {base.toFixed(2)}
                  </div>
                  <div className={"text-[10px] " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {up ? "+" : ""}
                    {chg.toFixed(2)}%
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1.5 mt-1.5">
                {badges.map((b, i) => (
                  <span key={i} className={"chip " + b.cls}>
                    {b.text}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div className="border-t border-ink-800 p-3 grad-card">
        <div className="flex items-center justify-between mb-2">
          <span className="tag text-ink-500">今日扫描 · {items.length} 只</span>
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
