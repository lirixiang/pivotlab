import { useEffect, useState } from "react";
import type { MarketOverview } from "../types";
import { api } from "../services/api";

export type TabKey = "workspace" | "screener" | "backtest" | "monitor";

const TABS: { k: TabKey; l: string }[] = [
  { k: "workspace", l: "画线工作台" },
  { k: "screener", l: "形态筛选" },
  { k: "backtest", l: "历史回测" },
  { k: "monitor", l: "自选监控" },
];

export function TopBar({
  tab,
  onTabChange,
}: {
  tab: TabKey;
  onTabChange: (t: TabKey) => void;
}) {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex flex-wrap items-center min-h-12 px-4 gap-x-4 gap-y-2 py-1.5">
      <div className="flex items-center gap-2 pr-4 border-r border-ink-700">
        <div className="w-7 h-7 rounded-md grad-gold flex items-center justify-center text-ink-950 font-bold">
          <i className="fas fa-wave-square text-xs" />
        </div>
        <div className="leading-tight">
          <div className="text-[13px] font-semibold tracking-wide text-white">
            PivotLab <span className="text-gold font-normal">智线</span>
          </div>
          <div className="text-[10px] text-ink-500 -mt-0.5 tracking-widest">
            AUTO LEVELS · SCREENER
          </div>
        </div>
      </div>

      <nav className="flex items-center gap-1 text-[13px]">
        {TABS.map((t) => (
          <button
            key={t.k}
            onClick={() => onTabChange(t.k)}
            className={
              "px-3 py-1.5 rounded-md transition " +
              (tab === t.k
                ? "text-white bg-ink-800 ring-soft"
                : "text-ink-500 hover:text-ink-200")
            }
          >
            {t.l}
          </button>
        ))}
      </nav>

      <div className="flex-1 min-w-[240px] flex justify-center">
        <div className="relative w-full max-w-[420px]">
          <i className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-ink-500 text-xs" />
          <input
            className="w-full bg-ink-850 border border-ink-700 rounded-md pl-9 pr-20 py-1.5 text-sm placeholder:text-ink-500 focus:outline-none focus:border-gold/60"
            placeholder="搜索代码 / 名称 / 行业    例如：600519 贵州茅台"
          />
          <span className="kbd absolute right-2 top-1/2 -translate-y-1/2">⌘K</span>
        </div>
      </div>

      <div className="flex items-center gap-3 text-ink-500">
        <div className="flex items-center gap-2 text-[11px]">
          <span className="dot bg-cn-dn" />
          <span className="text-ink-300">数据已同步</span>
          <span className="num">{time.toLocaleTimeString("zh-CN", { hour12: false })}</span>
        </div>
        <div className="w-px h-5 bg-ink-700" />
        <button className="hover:text-white text-sm">
          <i className="far fa-bell" />
        </button>
        <button className="hover:text-white text-sm">
          <i className="fas fa-sliders" />
        </button>
        <div className="w-7 h-7 rounded-full bg-ink-700 ring-soft flex items-center justify-center text-xs text-ink-300">
          IV
        </div>
      </div>
    </div>
  );
}

export function IndexStrip() {
  const [data, setData] = useState<MarketOverview | null>(null);
  useEffect(() => {
    api.market().then(setData).catch(() => {});
  }, []);
  const indices = data?.indices ?? [];
  return (
    <div className="flex items-center h-9 px-4 gap-6 border-t border-ink-800 text-[12px] num">
      {indices.map((i) => {
        const up = i.change_pct >= 0;
        return (
          <div key={i.code} className="flex items-center gap-2">
            <span className="text-ink-500">{i.name}</span>
            <span className={up ? "text-cn-up" : "text-cn-dn"}>{i.price.toFixed(2)}</span>
            <span className={"text-[11px] " + (up ? "text-cn-up" : "text-cn-dn")}>
              {up ? "+" : ""}
              {i.change_pct.toFixed(2)}%
            </span>
          </div>
        );
      })}
      <div className="flex items-center gap-2">
        <span className="text-ink-500">两市成交</span>
        <span className="text-ink-200">{data?.total_amount?.toFixed(0) ?? "—"}</span>
        <span className="text-ink-500 text-[11px]">亿</span>
      </div>
      <div className="flex-1" />
      <div className="flex items-center gap-2 text-ink-500">
        <i className="fas fa-circle-info text-[10px]" />
        <span>开源数据 · akshare · 仅供研究 · 不构成投资建议</span>
      </div>
    </div>
  );
}
