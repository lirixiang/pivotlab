import { useEffect, useRef, useState } from "react";
import type { MarketOverview } from "../types";
import { api } from "../services/api";

// NOTE (M0 重构): 重新设计为完整量化系统闭环。
// 旧 tab 暂时注释，待新模块（system/journal）稳定后于 M6 删除。
// 旧 tab keys: "recommend" | "screener" | "aiscan" | "llmpick" | "backtest" | "strategy" | "monitor"
export type TabKey = "workspace" | "system" | "sector" | "journal" | "sync" | "agent" | "jqstrategy" | "screener";

const TABS: { k: TabKey; l: string }[] = [
  { k: "workspace", l: "画线工作台" },
  { k: "system", l: "交易系统" },
  { k: "jqstrategy", l: "策略研究" },
  { k: "screener", l: "形态筛选" },
  { k: "sector", l: "赛道池" },
  { k: "journal", l: "实盘日志" },
  { k: "agent", l: "AI对话" },
  { k: "sync", l: "数据同步" },
  // ── 旧 tab（M0 注释，M6 删除）──
  // { k: "recommend", l: "今日推荐" },
  // { k: "aiscan", l: "AI选股" },
  // { k: "llmpick", l: "AI精选" },
  // { k: "backtest", l: "历史回测" },
  // { k: "strategy", l: "策略引擎" },
  // { k: "monitor", l: "自选" },
];

type StockItem = { code: string; name: string; industry: string };

export function TopBar({
  tab,
  onTabChange,
  onSearch,
}: {
  tab: TabKey;
  onTabChange: (t: TabKey) => void;
  onSearch?: (code: string) => void;
}) {
  const [time, setTime] = useState(new Date());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StockItem[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // Debounced remote search
  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults([]); return; }
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      api.searchStocks(q, 15).then((r) => {
        setResults(r);
        setActiveIdx(0);
      }).catch(() => {});
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [query]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Keyboard shortcut: Cmd+K / Ctrl+K
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const pick = (code: string) => {
    onSearch?.(code);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx((i) => Math.min(i + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter" && results[activeIdx]) { e.preventDefault(); pick(results[activeIdx].code); }
    else if (e.key === "Escape") { setOpen(false); }
  };
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
        <div className="relative w-full max-w-[420px]" ref={wrapRef}>
          <i className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-ink-500 text-xs" />
          <input
            ref={inputRef}
            className="w-full bg-ink-850 border border-ink-700 rounded-md pl-9 pr-20 py-1.5 text-sm placeholder:text-ink-500 focus:outline-none focus:border-gold/60"
            placeholder="搜索代码 / 名称 / 行业    例如：600519 贵州茅台"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
          />
          <span className="kbd absolute right-2 top-1/2 -translate-y-1/2">⌘K</span>

          {open && query.trim() && results.length > 0 && (
            <div className="absolute left-0 right-0 top-full mt-1 bg-ink-900 border border-ink-700 rounded-lg shadow-2xl z-50 overflow-hidden max-h-[360px] overflow-y-auto">
              {results.map((s, i) => (
                <button
                  key={s.code}
                  className={
                    "w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition " +
                    (i === activeIdx ? "bg-ink-800 text-white" : "text-ink-300 hover:bg-ink-850 hover:text-white")
                  }
                  onMouseEnter={() => setActiveIdx(i)}
                  onClick={() => pick(s.code)}
                >
                  <span className="num text-ink-500 w-16 text-[12px]">{s.code}</span>
                  <span className="flex-1 font-medium">{s.name}</span>
                  <span className="chip text-[10px]">{s.industry}</span>
                </button>
              ))}
            </div>
          )}
          {open && query.trim() && results.length === 0 && (
            <div className="absolute left-0 right-0 top-full mt-1 bg-ink-900 border border-ink-700 rounded-lg shadow-2xl z-50 p-4 text-center text-ink-500 text-sm">
              未找到匹配的股票
            </div>
          )}
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

/* ── Disclaimer popup ── */
function DisclaimerBadge() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 text-ink-500 hover:text-ink-300 transition"
        title="数据声明"
      >
        <i className="fas fa-shield-halved text-[10px]" />
        <span className="text-[11px]">数据声明</span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-[999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <div
            className="w-[420px] rounded-2xl bg-ink-850 border border-ink-700 shadow-2xl p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center">
                  <i className="fas fa-shield-halved text-amber-400 text-sm" />
                </div>
                <span className="text-sm font-medium text-ink-100">数据声明</span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="text-ink-500 hover:text-ink-200 transition text-lg leading-none"
              >
                ×
              </button>
            </div>

            {/* Content */}
            <div className="space-y-3 text-[13px] text-ink-300 leading-relaxed">
              <div className="flex gap-3">
                <i className="fas fa-database text-ink-500 mt-0.5 text-xs" />
                <span>本平台使用 <strong className="text-ink-200">开源数据接口</strong>（akshare / 东方财富 / 腾讯财经），数据仅供个人学习研究使用。</span>
              </div>
              <div className="flex gap-3">
                <i className="fas fa-triangle-exclamation text-amber-500/70 mt-0.5 text-xs" />
                <span>所有分析结果、形态识别、买卖信号均为 <strong className="text-ink-200">算法自动生成</strong>，不构成任何投资建议。</span>
              </div>
              <div className="flex gap-3">
                <i className="fas fa-user-shield text-ink-500 mt-0.5 text-xs" />
                <span>投资有风险，入市需谨慎。请根据自身情况独立判断，盈亏自负。</span>
              </div>
            </div>

            {/* Footer */}
            <button
              onClick={() => setOpen(false)}
              className="w-full rounded-lg bg-ink-700 hover:bg-ink-600 text-ink-200 text-sm py-2 transition"
            >
              我已知晓
            </button>
          </div>
        </div>
      )}
    </>
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
      <DisclaimerBadge />
    </div>
  );
}
