import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import { useUrlParam } from "../utils/useUrlParam";
import type { ScreenerItem, ScreenerResponse, SyncTask } from "../types";

const PATTERNS = [
  { key: "breakout_pullback", label: "突破回踩", color: "gold" },
  { key: "macd_divergence", label: "MACD底背离", color: "violet" },
  { key: "stage2_breakout", label: "Stage 2 突破", color: "blue" },
  { key: "vcp", label: "VCP 波动收缩", color: "indigo" },
  { key: "pivot_breakout", label: "Pivot 点突破", color: "cyan" },
  { key: "cup_handle", label: "杯柄形态", color: "pink" },
  { key: "high_tight_flag", label: "高位紧旗", color: "amber" },
  { key: "ma_support", label: "均线支撑", color: "sky" },
  { key: "volume_shrink_consolidation", label: "缩量整理", color: "slate" },
  { key: "trend_strong", label: "强势趋势", color: "emerald" },
  { key: "volume_breakout_resistance", label: "放量突破压力位", color: "rose" },
];

interface PatternGuide {
  desc: string;
  entry: string;
  stop: string;
  target: string;
  hold: string;
  risk: string;
}

const PATTERN_GUIDES: Record<string, PatternGuide> = {
  breakout_pullback: {
    desc: "前期突破压力位后回踩支撑位获支撑，是较经典的二次买点",
    entry: "回踩MA20/支撑位企稳后第1根阳线（量能温和回升）",
    stop: "支撑位下方 2-3%（破位即止损）",
    target: "前期高点或下一压力位，盈亏比≥2:1",
    hold: "5-15 个交易日（短中线）",
    risk: "若回踩跌破支撑则形态破坏；大盘下跌时假回踩概率高",
  },
  macd_divergence: {
    desc: "价格创新低但 MACD 未创新低，多头力量积聚",
    entry: "底背离形成 + 第1根放量阳线确认（金叉信号更佳）",
    stop: "前期低点下方 2-3%",
    target: "前期高点或下降趋势线",
    hold: "10-30 个交易日（中线）",
    risk: "底背离可能持续多次，单次背离失败率较高，需结合形态确认",
  },
  stage2_breakout: {
    desc: "Weinstein 阶段2突破：经长期 Stage 1 底部后向上突破 30周均线",
    entry: "30周均线上方 + 突破前期高点 + 周线放量",
    stop: "跌破 30周均线（约 -7~-10%）",
    target: "趋势走坏前持续持有（追踪止损）",
    hold: "数月至 1 年以上（中长线）",
    risk: "假突破后回到 Stage 1 概率不低；牛市末期突破易失败",
  },
  vcp: {
    desc: "Minervini 波动收缩形态：价量逐步收窄，主力筹码锁定",
    entry: "Pivot 点突破当天放量（量比 ≥1.5×）",
    stop: "Pivot 点下方 5-7%（最大不超过 10%）",
    target: "翻倍或趋势走坏；可设 +20%/+25% 阶梯减仓",
    hold: "数周至数月（趋势跟随）",
    risk: "需大盘配合，市场环境差时假突破多",
  },
  pivot_breakout: {
    desc: "O'Neil CAN SLIM Pivot 点（关键阻力点）放量突破",
    entry: "突破 Pivot 价当天（不要追超 +5%）",
    stop: "Pivot 下方 5-7%（也可用 -8% 硬止损）",
    target: "+20% ~ +25% 减仓，剩余持有 8 周",
    hold: "4-8 周（短中线）",
    risk: "0day 假突破常见；最好次日确认",
  },
  cup_handle: {
    desc: "O'Neil 杯柄：U 型底 + 右侧小回调形成把手，把手末端突破",
    entry: "把手末端放量突破（量比 ≥1.5×）",
    stop: "把手最低点下方",
    target: "杯深的 1 倍空间（量出突破点 +杯深）",
    hold: "数周至数月",
    risk: "把手过深（>15%）形态破坏；杯型不规则不算",
  },
  high_tight_flag: {
    desc: "短期暴涨后小幅高位整理，旗形上沿突破即续涨",
    entry: "旗形上沿突破当天",
    stop: "旗形下沿（约 -5%）",
    target: "前期上涨段等长度（量出空间）",
    hold: "1-3 周（短线）",
    risk: "高位形态情绪化重；一旦放量下破立即清仓",
  },
  ma_support: {
    desc: "回踩关键均线（MA20/MA60）获得支撑，是趋势中的二次进场点",
    entry: "触及均线后下影线 + 次日企稳阳线",
    stop: "均线下方 3% 或前低",
    target: "前高或下一压力位",
    hold: "5-15 个交易日",
    risk: "趋势走弱时连续破均线；需结合大盘判断",
  },
  volume_shrink_consolidation: {
    desc: "缩量横盘整理，等待方向选择",
    entry: "放量启动 + 突破整理区上沿",
    stop: "整理区下沿",
    target: "整理区高度的 1-1.5 倍空间",
    hold: "3-10 个交易日",
    risk: "可能向下突破；建议等放量信号确认再进",
  },
  trend_strong: {
    desc: "MA10>MA20>MA60 多头排列，趋势明确向上",
    entry: "回踩 MA10/MA20 不破时加仓",
    stop: "跌破 MA20 减仓，跌破 MA60 全部清仓",
    target: "趋势走坏前持续持有",
    hold: "中长线",
    risk: "趋势末期假回调难辨；高位追涨风险大",
  },
  volume_breakout_resistance: {
    desc: "放量突破关键压力位，主力资金强势介入信号",
    entry: "突破当天追入或次日小回不破压力位时进场",
    stop: "压力位下方 2-3%（破位即止损）",
    target: "下一压力位或测算空间（突破前盘整高度的 1-1.5 倍）",
    hold: "3-10 个交易日（短线）",
    risk: "假突破率较高（约 30%）；需关注次日量能持续性",
  },
};

type SortKey =
  | "score" | "change_pct" | "volume_ratio" | "distance" | "price"
  | "rr_ratio" | "support_score" | "amount"
  | "name" | "market" | "industry" | "concept" | "fundamental" | "triggers";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; defaultDir: SortDir }[] = [
  { key: "name", label: "代码/名称", defaultDir: "asc" },
  { key: "market", label: "市场", defaultDir: "asc" },
  { key: "industry", label: "行业", defaultDir: "asc" },
  { key: "concept", label: "概念", defaultDir: "asc" },
  { key: "fundamental", label: "基本面", defaultDir: "desc" },
  { key: "score", label: "信号强度", defaultDir: "desc" },
  { key: "price", label: "现价", defaultDir: "desc" },
  { key: "change_pct", label: "涨跌幅", defaultDir: "desc" },
  { key: "amount", label: "成交额", defaultDir: "desc" },
  { key: "volume_ratio", label: "量比", defaultDir: "desc" },
  { key: "distance", label: "距支撑", defaultDir: "asc" },
  { key: "rr_ratio", label: "盈亏比", defaultDir: "desc" },
  { key: "support_score", label: "支撑强度", defaultDir: "desc" },
  { key: "triggers", label: "触发条件", defaultDir: "desc" },
];

const FUND_RANK: Record<string, number> = { healthy: 4, neutral: 3, weak: 2, risk: 1, unknown: 0, "": 0 };

export function ScreenerPage({
  onPickStock,
  onShowRecommend,
  onAIAnalyze,
}: {
  onPickStock: (code: string) => void;
  onShowRecommend?: (code: string) => void;
  onAIAnalyze?: (code: string, extra: string) => void;
}) {
  const [pattern, setPattern] = useUrlParam<string>("pattern", "breakout_pullback");
  const [data, setData] = useState<Record<string, ScreenerResponse>>({});
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<{ processed: number; total: number } | null>(null);
  const [minScore, setMinScore] = useState(0);
  const [sortKey, setSortKey] = useUrlParam<SortKey>("sort", "score");
  const [sortDir, setSortDir] = useUrlParam<SortDir>("dir", "desc");
  const [history, setHistory] = useState<{ ts: string; scanned_at: string; total: number; scanned: number }[]>([]);
  const [histOpen, setHistOpen] = useState(false);
  const [activeTs, setActiveTs] = useState<string | null>(null); // null = latest
  const [guideOpen, setGuideOpen] = useState(true);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const histRef = useRef<HTMLDivElement>(null);

  // Pre-filters
  const [fundFilter, setFundFilter] = useState<"all" | "healthy" | "healthy_neutral">("all");
  const [capFilter, setCapFilter] = useState<"all" | "large" | "mid" | "small">("all");
  const [industryFilter, setIndustryFilter] = useState<string>("all");
  const [industryOpen, setIndustryOpen] = useState(false);
  const industryRef = useRef<HTMLDivElement>(null);

  const fetchResults = useCallback(() => {
    setLoading(true);
    Promise.all(
      PATTERNS.map((p) => api.screener(p.key, 200))
    )
      .then((results) => {
        const obj: Record<string, ScreenerResponse> = {};
        PATTERNS.forEach((p, i) => { obj[p.key] = results[i]; });
        setData(obj);
      })
      .finally(() => setLoading(false));
  }, []);

  const fetchHistory = useCallback(() => {
    api.screenerHistory(pattern).then(setHistory).catch(() => {});
  }, [pattern]);

  useEffect(() => { fetchResults(); }, [fetchResults]);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Close history dropdown on outside click
  useEffect(() => {
    if (!histOpen) return;
    const handler = (e: MouseEvent) => {
      if (histRef.current && !histRef.current.contains(e.target as Node)) setHistOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [histOpen]);

  // Close industry dropdown on outside click
  useEffect(() => {
    if (!industryOpen) return;
    const handler = (e: MouseEvent) => {
      if (industryRef.current && !industryRef.current.contains(e.target as Node)) setIndustryOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [industryOpen]);

  const runScan = async (scope: "current" | "all" = "current") => {
    setScanning(true);
    setScanProgress(null);
    setActiveTs(null);
    try {
      await api.triggerScan(scope === "current" ? pattern : undefined);
      // Poll progress from sync tasks
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const tasks: SyncTask[] = await api.syncTasks();
          const screenerTask = tasks.find((t) => t.task_type === "screener");
          if (screenerTask) {
            if (screenerTask.total > 0) {
              setScanProgress({ processed: screenerTask.processed, total: screenerTask.total });
            }
            if (screenerTask.status === "done" || screenerTask.status === "error") {
              break;
            }
          }
        } catch {}
      }
      // Final fetch
      fetchResults();
      fetchHistory();
    } finally {
      setScanning(false);
      setScanProgress(null);
    }
  };

  const loadSnapshot = async (ts: string) => {
    setLoading(true);
    setActiveTs(ts);
    setHistOpen(false);
    try {
      const snap = await api.screenerSnapshot(pattern, ts, 200);
      setData((prev) => ({ ...prev, [pattern]: snap }));
    } catch {}
    setLoading(false);
  };

  const loadLatest = () => {
    setActiveTs(null);
    setHistOpen(false);
    fetchResults();
  };

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      const col = COLUMNS.find((c) => c.key === key)!;
      setSortKey(key);
      setSortDir(col.defaultDir);
    }
  };

  const industries = useMemo(() => {
    const all = data[pattern]?.items ?? [];
    const set = new Set(all.map((i) => i.industry).filter(Boolean) as string[]);
    return Array.from(set).sort((a, b) => a.localeCompare(b, "zh"));
  }, [data, pattern]);

  const items: ScreenerItem[] = useMemo(() => {
    const raw = data[pattern]?.items ?? [];
    const preFiltered = raw.filter((i) => {
      if (fundFilter === "healthy" && i.fundamental_status !== "healthy") return false;
      if (fundFilter === "healthy_neutral" && !["healthy", "neutral"].includes(i.fundamental_status ?? "")) return false;
      if (capFilter === "large" && (i.market_cap == null || i.market_cap < 100)) return false;
      if (capFilter === "mid" && (i.market_cap == null || i.market_cap < 20 || i.market_cap >= 100)) return false;
      if (capFilter === "small" && (i.market_cap == null || i.market_cap >= 20)) return false;
      if (industryFilter !== "all" && i.industry !== industryFilter) return false;
      return true;
    });
    const filtered = minScore > 0 ? preFiltered.filter((i) => i.score >= minScore) : preFiltered;
    const mul = sortDir === "desc" ? -1 : 1;
    const getVal = (it: ScreenerItem): number | string => {
      switch (sortKey) {
        case "score": return it.score;
        case "price": return it.price;
        case "change_pct": return it.change_pct;
        case "volume_ratio": return it.volume_ratio;
        case "amount": return it.amount ?? 0;
        case "rr_ratio": return it.rr_ratio ?? 0;
        case "support_score": return it.support_score ?? 0;
        case "distance": return it.distance_to_support_pct ?? 999;
        case "name": return it.name || it.code;
        case "market": return it.market || "";
        case "industry": return it.industry || "";
        case "concept": return it.concept || "";
        case "fundamental": return FUND_RANK[it.fundamental_status ?? ""] ?? 0;
        case "triggers": return it.triggers?.length ?? 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const va = getVal(a), vb = getVal(b);
      if (typeof va === "string" && typeof vb === "string") return va.localeCompare(vb) * mul;
      return ((va as number) - (vb as number)) * mul;
    });
  }, [data, pattern, minScore, sortKey, sortDir, fundFilter, capFilter, industryFilter]);

  const counts: Record<string, number> = {};
  PATTERNS.forEach((p) => { counts[p.key] = data[p.key]?.total ?? 0; });
  const scannedAt = data[pattern]?.scanned_at;
  const scanned = data[pattern]?.scanned ?? 0;
  const isBreakout = pattern === "breakout_pullback";
  const high = items.filter((i) => i.score >= 80).length;
  const med = items.filter((i) => i.score >= 60 && i.score < 80).length;

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      {/* ── Top toolbar ── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-ink-800 grad-head flex-wrap gap-y-2">
        <div className="flex items-center gap-4">
          {/* Pattern tabs */}
          <div className="seg">
            {PATTERNS.map((p) => (
              <button
                key={p.key}
                className={pattern === p.key ? "on" : ""}
                onClick={() => setPattern(p.key)}
              >
                <span className={"dot mr-1.5 " + (p.color === "gold" ? "bg-gold" : "bg-sky2")} />
                {p.label}
                <span className="num text-ink-500 ml-1.5">{counts[p.key] ?? 0}</span>
              </button>
            ))}
          </div>

          {/* Min score filter */}
          <div className="flex items-center gap-2 text-[11px] text-ink-400">
            <span>≥</span>
            <input
              type="range" min={0} max={100} step={5} value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="w-20 accent-gold"
            />
            <span className="num text-ink-200 w-5 text-right">{minScore}</span>
            <span>分</span>
          </div>

          {/* Stats pills */}
          <div className="flex items-center gap-2 text-[11px]">
            <span className="chip"><span className="text-gold num mr-1">{high}</span> 高强</span>
            <span className="chip"><span className="text-sky2 num mr-1">{med}</span> 中等</span>
            <span className="chip"><span className="text-ink-100 num mr-1">{items.length}</span> 入选</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {scannedAt && (
            <span className="text-[10px] text-ink-500">
              上次扫描 {scannedAt.replace("T", " ").slice(5, 16)} · {scanned} 只
            </span>
          )}
          {/* History dropdown */}
          <div className="relative" ref={histRef}>
            <button
              className="px-2 py-1.5 text-[11px] rounded-md border border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600 transition"
              onClick={() => { if (!histOpen) fetchHistory(); setHistOpen(!histOpen); }}
            >
              <i className="fas fa-clock-rotate-left mr-1 text-[9px]" />
              {activeTs ? `${activeTs.slice(4,6)}-${activeTs.slice(6,8)} ${activeTs.slice(9,11)}:${activeTs.slice(11)}` : "历史"}
              <i className="fas fa-chevron-down ml-1 text-[8px]" />
            </button>
            {histOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-64 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 max-h-72 overflow-y-auto scrollbar animate-in fade-in slide-in-from-top-1 duration-150">
                <button
                  className={`w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition flex items-center gap-2 ${!activeTs ? "text-gold" : "text-ink-300"}`}
                  onClick={loadLatest}
                >
                  <i className={`fas fa-${activeTs ? "arrow-left" : "check"} text-[9px] w-3`} />
                  <span className="font-medium">最新结果</span>
                </button>
                <div className="border-t border-ink-700 my-1" />
                {history.length === 0 && (
                  <div className="px-3 py-4 text-[10px] text-ink-600 text-center">暂无历史记录</div>
                )}
                {history.map((h) => (
                  <button
                    key={h.ts}
                    className={`w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition flex items-center justify-between ${activeTs === h.ts ? "text-gold bg-ink-800/50" : "text-ink-300"}`}
                    onClick={() => loadSnapshot(h.ts)}
                  >
                    <span>
                      {activeTs === h.ts && <i className="fas fa-check text-[9px] mr-1.5" />}
                      {h.scanned_at.replace("T", " ").slice(5, 16)}
                    </span>
                    <span className="text-ink-500 num">{h.total} 只入选</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              className="px-3 py-1.5 text-[12px] rounded-l-md grad-gold text-ink-950 font-semibold disabled:opacity-50"
              onClick={() => runScan("current")}
              disabled={scanning}
              title={`仅扫描当前选中的形态(约 3810 只×1 形态,快 5 倍)`}
            >
              {scanning ? (
                <><i className="fas fa-circle-notch fa-spin mr-1" /> 扫描中...</>
              ) : (
                <><i className="fas fa-bolt mr-1" /> 当前形态</>
              )}
            </button>
            <button
              className="px-2 py-1.5 text-[12px] rounded-r-md bg-ink-800 hover:bg-ink-700 text-ink-200 disabled:opacity-50 border-l border-ink-900"
              onClick={() => runScan("all")}
              disabled={scanning}
              title="扫描全部 5 个形态 (约 19050 任务，较慢)"
            >
              <i className="fas fa-layer-group mr-1" />全部
            </button>
          </div>
          <button
            className={"px-2 py-1.5 text-[11px] rounded-md border transition " +
              (guideOpen
                ? "border-gold/60 text-gold bg-gold/10"
                : "border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600")}
            onClick={() => setGuideOpen(!guideOpen)}
            title="显示/隐藏 形态使用指南"
          >
            <i className="fas fa-book-open mr-1 text-[9px]" />
            形态指南
          </button>
        </div>
      </div>

      {/* ── Pre-filter bar ── */}
      <div className="flex items-center gap-4 px-5 py-2 border-b border-ink-800 bg-ink-900/30 flex-wrap">
        {/* 基本面 */}
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="text-ink-500">基本面</span>
          <div className="seg">
            {(["all", "healthy_neutral", "healthy"] as const).map((v) => (
              <button key={v} className={fundFilter === v ? "on" : ""} onClick={() => setFundFilter(v)}>
                {v === "all" ? "全部" : v === "healthy_neutral" ? "良好+中性" : "仅良好"}
              </button>
            ))}
          </div>
        </div>

        <div className="h-3 w-px bg-ink-700" />

        {/* 市值 */}
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="text-ink-500">市值</span>
          <div className="seg">
            {(["all", "large", "mid", "small"] as const).map((v) => (
              <button key={v} className={capFilter === v ? "on" : ""} onClick={() => setCapFilter(v)}>
                {v === "all" ? "全部" : v === "large" ? ">100亿" : v === "mid" ? "20~100亿" : "<20亿"}
              </button>
            ))}
          </div>
        </div>

        <div className="h-3 w-px bg-ink-700" />

        {/* 行业 */}
        <div className="flex items-center gap-1.5 text-[11px]" ref={industryRef}>
          <span className="text-ink-500">行业</span>
          <div className="relative">
            <button
              className={"px-2.5 py-1 rounded-md border text-[11px] transition flex items-center gap-1 " +
                (industryFilter !== "all"
                  ? "border-gold/60 text-gold bg-gold/10"
                  : "border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600")}
              onClick={() => setIndustryOpen(!industryOpen)}
            >
              {industryFilter === "all" ? "全部" : industryFilter}
              <i className="fas fa-chevron-down text-[8px]" />
            </button>
            {industryOpen && (
              <div className="absolute left-0 top-full mt-1 z-50 w-44 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 max-h-64 overflow-y-auto scrollbar animate-in fade-in slide-in-from-top-1 duration-150">
                <button
                  className={"w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition " + (industryFilter === "all" ? "text-gold" : "text-ink-300")}
                  onClick={() => { setIndustryFilter("all"); setIndustryOpen(false); }}
                >
                  全部行业
                </button>
                <div className="border-t border-ink-700 my-1" />
                {industries.map((ind) => (
                  <button
                    key={ind}
                    className={"w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition " + (industryFilter === ind ? "text-gold bg-ink-800/50" : "text-ink-300")}
                    onClick={() => { setIndustryFilter(ind); setIndustryOpen(false); }}
                  >
                    {ind}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Active filter count */}
        {(fundFilter !== "all" || capFilter !== "all" || industryFilter !== "all") && (
          <>
            <div className="h-3 w-px bg-ink-700" />
            <button
              className="text-[11px] text-ink-500 hover:text-cn-dn transition flex items-center gap-1"
              onClick={() => { setFundFilter("all"); setCapFilter("all"); setIndustryFilter("all"); }}
            >
              <i className="fas fa-times text-[9px]" />
              清除筛选
            </button>
          </>
        )}
      </div>

      {/* ── Pattern usage guide ── */}
      {guideOpen && PATTERN_GUIDES[pattern] && (
        <div className="px-5 py-3 border-b border-ink-800 bg-ink-900/40">
          <div className="flex items-start gap-3">
            <div className="flex-shrink-0 w-1 self-stretch rounded-full bg-gradient-to-b from-gold to-gold/30" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[12px] font-semibold text-gold">
                  {PATTERNS.find((p) => p.key === pattern)?.label}
                </span>
                <span className="text-[10px] text-ink-500">{PATTERN_GUIDES[pattern].desc}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-x-4 gap-y-1.5 text-[11px]">
                <GuideRow icon="sign-in-alt" label="入场" text={PATTERN_GUIDES[pattern].entry} color="text-cn-up" />
                <GuideRow icon="hand-paper" label="止损" text={PATTERN_GUIDES[pattern].stop} color="text-cn-dn" />
                <GuideRow icon="bullseye" label="止盈" text={PATTERN_GUIDES[pattern].target} color="text-gold" />
                <GuideRow icon="clock" label="持有" text={PATTERN_GUIDES[pattern].hold} color="text-sky2" />
                <GuideRow icon="exclamation-triangle" label="风险" text={PATTERN_GUIDES[pattern].risk} color="text-yellow-400" />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Progress bar ── */}
      {scanning && scanProgress && scanProgress.total > 0 && (
        <div className="px-5 py-2 border-b border-ink-800 bg-ink-900/50">
          <div className="flex items-center justify-between text-[11px] mb-1.5">
            <span className="text-ink-400">
              <i className="fas fa-circle-notch fa-spin mr-1.5 text-[9px] text-gold" />
              正在扫描...
            </span>
            <span className="num text-ink-300">
              {scanProgress.processed} / {scanProgress.total}
              <span className="text-ink-500 ml-1.5">
                ({Math.round(scanProgress.processed / scanProgress.total * 100)}%)
              </span>
            </span>
          </div>
          <div className="w-full h-1.5 bg-ink-800 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700 ease-out"
              style={{
                width: `${Math.max(1, scanProgress.processed / scanProgress.total * 100)}%`,
                background: "linear-gradient(90deg, #d4a857, #f0c674)",
              }}
            />
          </div>
        </div>
      )}

      {/* ── Results table ── */}
      <div className="overflow-y-auto scrollbar flex-1">
        <table className="w-full text-[12px] num">
          <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
            <tr className="border-b border-ink-800">
              <th className="text-left font-normal px-5 py-2.5 w-8">#</th>
              <SortTh k="name" label="代码 / 名称" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" />
              <SortTh k="market" label="市场" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" />
              <SortTh k="industry" label="行业" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" />
              <SortTh k="concept" label="概念" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" />
              <SortTh k="fundamental" label="基本面" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" />
              <SortTh k="price" label="现价" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="change_pct" label="涨跌幅" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="amount" label="成交额(万)" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="distance" label="距支撑" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="rr_ratio" label="盈亏比" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="support_score" label="支撑强度" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="volume_ratio" label="量比" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="right" />
              <SortTh k="score" label="信号强度" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" className="w-36" />
              <SortTh k="triggers" label="触发条件" sortKey={sortKey} sortDir={sortDir} onClick={handleSort} align="left" />
              <th className="text-right font-normal px-2 w-20">推荐</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {(loading || scanning) && items.length === 0 && (
              <tr>
                <td colSpan={16} className="text-center text-ink-500 py-16">
                  <i className="fas fa-circle-notch fa-spin text-xl text-gold mb-3 block" />
                  {scanning
                    ? scanProgress && scanProgress.total > 0
                      ? `正在扫描 ${scanProgress.processed}/${scanProgress.total}...`
                      : "正在准备扫描，可能需要先更新行情数据..."
                    : "加载中..."}
                </td>
              </tr>
            )}
            {!loading && !scanning && items.length === 0 && (
              <tr>
                <td colSpan={16} className="text-center text-ink-500 py-16">
                  <i className="fas fa-binoculars text-2xl text-ink-700 block mb-3" />
                  <div>暂无结果</div>
                  <div className="mt-1 text-[11px]">
                    点击「全市场扫描」开始扫描，或降低最低信号强度
                  </div>
                </td>
              </tr>
            )}
            {items.map((it, i) => {
              const up = it.change_pct >= 0;
              const scoreColor = it.score >= 80 ? "text-gold" : it.score >= 60 ? "text-sky2" : "text-ink-400";
              const barColor = isBreakout
                ? "linear-gradient(90deg,#d4a857,#f0c674)"
                : "linear-gradient(90deg,#7dd3fc,#bae6fd)";
              const isExpanded = expandedCode === it.code;
              return (
                <Fragment key={it.code}>
                <tr
                  className={"row-hover border-b border-ink-850/70 cursor-pointer " + (isExpanded ? "bg-ink-900/60" : "")}
                  onClick={() => setExpandedCode(isExpanded ? null : it.code)}
                >
                  <td className="px-5 py-2.5 text-ink-500">
                    <i className={"fas fa-chevron-right text-[8px] mr-1.5 transition-transform " + (isExpanded ? "rotate-90 text-gold" : "text-ink-600")} />
                    {i + 1}
                  </td>
                  <td className="px-2">
                    <div className="flex items-center gap-2">
                      <div>
                        <span className="font-sans text-ink-100">{it.name}</span>
                        <span className="text-[10px] text-ink-500 ml-1.5">{it.code}</span>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); window.open(`/stock/${it.code}`, "_blank"); }}
                        className="text-ink-600 hover:text-sky2 text-[10px]"
                        title="新标签页打开 K线"
                      >
                        <i className="fas fa-external-link-alt" />
                      </button>
                    </div>
                  </td>
                  <td className="px-2 text-ink-400 text-[11px]">{it.market || "—"}</td>
                  <td className="px-2 text-ink-400 text-[11px]">{it.industry || "—"}</td>
                  <td className="px-2 text-ink-400 text-[11px] max-w-[100px] truncate" title={it.concept || ""}>{it.concept || "—"}</td>
                  <td className="px-2 text-[11px]">
                    {it.fundamental_status === "healthy" ? (
                      <span className="text-cn-up" title={it.fundamental_summary}>良好</span>
                    ) : it.fundamental_status === "neutral" ? (
                      <span className="text-ink-300" title={it.fundamental_summary}>中性</span>
                    ) : it.fundamental_status === "weak" ? (
                      <span className="text-yellow-400" title={it.fundamental_summary}>偏弱</span>
                    ) : it.fundamental_status === "risk" ? (
                      <span className="text-cn-dn" title={it.fundamental_summary}>风险</span>
                    ) : (
                      <span className="text-ink-500">—</span>
                    )}
                  </td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {it.price.toFixed(2)}
                  </td>
                  <td className={"text-right px-2 " + (up ? "text-cn-up" : "text-cn-dn")}>
                    {up ? "+" : ""}{it.change_pct.toFixed(2)}%
                  </td>
                  <td className="text-right px-2 text-ink-300">
                    {it.amount != null ? it.amount.toLocaleString() : "—"}
                  </td>
                  <td className="text-right px-2 text-cn-dn">
                    {it.distance_to_support_pct != null
                      ? (it.distance_to_support_pct >= 0 ? "+" : "") + it.distance_to_support_pct.toFixed(2) + "%"
                      : "—"}
                  </td>
                  <td className="text-right px-2">
                    <span className={it.rr_ratio != null && it.rr_ratio >= 3 ? "text-gold" : "text-ink-300"}>
                      {it.rr_ratio != null ? it.rr_ratio.toFixed(1) : "—"}
                    </span>
                  </td>
                  <td className="text-right px-2">
                    <span className={it.support_score != null && it.support_score >= 70 ? "text-gold" : "text-ink-300"}>
                      {it.support_score != null ? Math.round(it.support_score) : "—"}
                    </span>
                  </td>
                  <td className="text-right px-2">
                    <span className={it.volume_ratio >= 1.5 ? "text-gold" : ""}>
                      {it.volume_ratio.toFixed(2)}
                    </span>
                  </td>
                  <td className="px-2">
                    <div className="flex items-center gap-2">
                      <div className="level-bar flex-1 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: Math.min(100, it.score) + "%", background: barColor }}
                        />
                      </div>
                      <span className={"w-6 text-right " + scoreColor}>{Math.round(it.score)}</span>
                    </div>
                  </td>
                  <td className="px-2">
                    <div className="flex flex-wrap gap-1">
                      {it.triggers.map((t, j) => (
                        <span key={j} className={"chip text-[10px] " + (isBreakout ? "chip-on" : "chip-dn")}>
                          {t}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-2 text-right">
                    {onShowRecommend && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onShowRecommend(it.code); }}
                        className="px-2 py-1 text-[10px] rounded bg-gold/20 text-gold hover:bg-gold/30 whitespace-nowrap"
                        title="跳转到选股页查看该股票的推荐"
                      >
                        <i className="fas fa-bullseye mr-1" />推荐
                      </button>
                    )}
                  </td>
                </tr>
                {isExpanded && (
                  <tr key={it.code + "_detail"} className="bg-ink-900/40 border-b border-ink-850/70">
                    <td colSpan={16} className="px-5 py-3">
                      <StockDetailPanel
                        item={it}
                        patternKey={pattern}
                        patternLabel={PATTERNS.find((p) => p.key === pattern)?.label || pattern}
                        onAIAnalyze={onAIAnalyze}
                        onPickStock={onPickStock}
                      />
                    </td>
                  </tr>
                )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Sortable column header ── */
function SortTh({ k, label, sortKey, sortDir, onClick, align = "left", className = "" }: {
  k: SortKey; label: string; sortKey: SortKey; sortDir: SortDir;
  onClick: (k: SortKey) => void; align?: "left" | "right"; className?: string;
}) {
  const active = sortKey === k;
  return (
    <th
      className={
        "font-normal px-2 py-2.5 cursor-pointer select-none transition " +
        (align === "right" ? "text-right " : "text-left ") +
        (active ? "text-ink-200" : "text-ink-500 hover:text-ink-300") +
        (className ? " " + className : "")
      }
      onClick={() => onClick(k)}
    >
      {label}
      {active && (
        <i className={"fas fa-caret-" + (sortDir === "desc" ? "down" : "up") + " ml-1 text-[9px] text-gold"} />
      )}
    </th>
  );
}

/* ── Pattern guide row ── */
function GuideRow({ icon, label, text, color }: { icon: string; label: string; text: string; color: string }) {
  return (
    <div className="flex items-start gap-1.5 min-w-0">
      <i className={"fas fa-" + icon + " " + color + " text-[10px] mt-0.5 flex-shrink-0"} />
      <span className={"font-semibold flex-shrink-0 " + color}>{label}</span>
      <span className="text-ink-300 truncate" title={text}>{text}</span>
    </div>
  );
}

/* ── Inline stock detail panel ── */
function StockDetailPanel({ item, patternKey, patternLabel, onAIAnalyze, onPickStock }: {
  item: ScreenerItem;
  patternKey: string;
  patternLabel: string;
  onAIAnalyze?: (code: string, extra: string) => void;
  onPickStock: (code: string) => void;
}) {
  // Compute position evaluation (using existing data)
  const stopPrice = item.pullback_price ?? (item.breakout_price ? item.breakout_price * 0.97 : null);
  const targetPrice = item.breakout_price && item.pullback_price
    ? item.price + (item.price - item.pullback_price) * (item.rr_ratio || 2)
    : null;
  const stopLossPct = stopPrice ? ((stopPrice - item.price) / item.price) * 100 : null;
  const targetPct = targetPrice ? ((targetPrice - item.price) / item.price) * 100 : null;

  // Volume-price match assessment
  let vpMatch = "—";
  let vpColor = "text-ink-400";
  if (item.volume_ratio >= 1.5 && item.change_pct > 1) {
    vpMatch = "量价齐升 ✓"; vpColor = "text-cn-up";
  } else if (item.volume_ratio >= 1.5 && item.change_pct < -1) {
    vpMatch = "放量下跌 ⚠"; vpColor = "text-cn-dn";
  } else if (item.volume_ratio < 0.7 && item.change_pct > 1) {
    vpMatch = "缩量上涨 ⚠"; vpColor = "text-yellow-400";
  } else if (item.volume_ratio < 0.7) {
    vpMatch = "缩量整理"; vpColor = "text-ink-300";
  } else {
    vpMatch = "正常"; vpColor = "text-ink-300";
  }

  // Trend strength rating
  const trendRating = item.score >= 80 ? "强势 ★★★" : item.score >= 60 ? "中等 ★★" : "偏弱 ★";
  const trendColor = item.score >= 80 ? "text-gold" : item.score >= 60 ? "text-sky2" : "text-ink-400";

  // Build screener-only context (will be merged into K-line page's full prompt)
  const buildExtra = () => {
    const lines: string[] = [];
    lines.push(`当前选股形态：${patternLabel}（信号强度 ${Math.round(item.score)}/100）`);
    if (item.triggers && item.triggers.length > 0) {
      lines.push(`触发条件：${item.triggers.join("、")}`);
    }
    const guide = PATTERN_GUIDES[patternKey];
    if (guide) {
      lines.push(`形态要点：${guide.desc}`);
      lines.push(`  · 标准入场：${guide.entry}`);
      lines.push(`  · 标准止损：${guide.stop}`);
      lines.push(`  · 标准止盈：${guide.target}`);
      lines.push(`  · 建议持有：${guide.hold}`);
      lines.push(`  · 主要风险：${guide.risk}`);
    }
    lines.push(`量价指标：量比=${item.volume_ratio.toFixed(2)}，涨跌幅=${item.change_pct >= 0 ? "+" : ""}${item.change_pct.toFixed(2)}%`);
    if (item.distance_to_support_pct != null) {
      lines.push(`位置评估：距支撑 ${item.distance_to_support_pct >= 0 ? "+" : ""}${item.distance_to_support_pct.toFixed(2)}%，支撑强度 ${item.support_score != null ? Math.round(item.support_score) : "-"}/100`);
    }
    if (item.rr_ratio != null) {
      lines.push(`盈亏比：${item.rr_ratio.toFixed(2)} : 1`);
    }
    if (item.breakout_price != null || item.pullback_price != null) {
      const parts: string[] = [];
      if (item.breakout_price != null) parts.push(`突破价=${item.breakout_price.toFixed(2)}`);
      if (item.pullback_price != null) parts.push(`回踩价=${item.pullback_price.toFixed(2)}`);
      lines.push(`关键价位：${parts.join("，")}`);
    }
    lines.push(``);
    lines.push(`请在上述形态评分与完整 K 线/基本面/机构数据的基础上，重点评估：当前价位是否适合按该形态买入、能否达到标准盈亏比、何时应该减仓/清仓。`);
    return lines.join("\n");
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 text-[12px]">
      {/* ── Section 1: Position Evaluation ── */}
      <div className="bg-ink-950/60 border border-ink-800 rounded-lg p-3">
        <div className="flex items-center gap-2 mb-2.5 pb-2 border-b border-ink-800">
          <i className="fas fa-chart-line text-sky2 text-[11px]" />
          <span className="font-semibold text-ink-100">位置评估</span>
        </div>
        <div className="space-y-1.5">
          <EvalRow label="入场参考" value={item.price.toFixed(2)} color="text-ink-100" />
          <EvalRow
            label="止损位"
            value={stopPrice ? stopPrice.toFixed(2) : "—"}
            sub={stopLossPct != null ? `${stopLossPct.toFixed(1)}%` : ""}
            color="text-cn-dn"
          />
          <EvalRow
            label="目标位"
            value={targetPrice ? targetPrice.toFixed(2) : "—"}
            sub={targetPct != null ? `+${targetPct.toFixed(1)}%` : ""}
            color="text-gold"
          />
          <EvalRow
            label="盈亏比"
            value={item.rr_ratio != null ? item.rr_ratio.toFixed(2) + " : 1" : "—"}
            color={item.rr_ratio != null && item.rr_ratio >= 3 ? "text-gold" : "text-ink-300"}
          />
          <EvalRow
            label="距支撑"
            value={item.distance_to_support_pct != null
              ? (item.distance_to_support_pct >= 0 ? "+" : "") + item.distance_to_support_pct.toFixed(2) + "%"
              : "—"}
            color={item.distance_to_support_pct != null && Math.abs(item.distance_to_support_pct) < 3 ? "text-gold" : "text-ink-300"}
          />
          <EvalRow
            label="支撑强度"
            value={item.support_score != null ? Math.round(item.support_score) + " / 100" : "—"}
            color={item.support_score != null && item.support_score >= 70 ? "text-gold" : "text-ink-300"}
          />
        </div>
      </div>

      {/* ── Section 2: Signal Strength ── */}
      <div className="bg-ink-950/60 border border-ink-800 rounded-lg p-3">
        <div className="flex items-center gap-2 mb-2.5 pb-2 border-b border-ink-800">
          <i className="fas fa-signal text-gold text-[11px]" />
          <span className="font-semibold text-ink-100">信号评估</span>
        </div>
        <div className="space-y-1.5">
          <EvalRow label="信号强度" value={Math.round(item.score) + " / 100"} color={trendColor} sub={trendRating} />
          <EvalRow
            label="量比"
            value={item.volume_ratio.toFixed(2)}
            color={item.volume_ratio >= 1.5 ? "text-gold" : item.volume_ratio < 0.7 ? "text-yellow-400" : "text-ink-300"}
          />
          <EvalRow label="量价匹配" value={vpMatch} color={vpColor} />
          <EvalRow
            label="基本面"
            value={
              item.fundamental_status === "healthy" ? "良好"
              : item.fundamental_status === "neutral" ? "中性"
              : item.fundamental_status === "weak" ? "偏弱"
              : item.fundamental_status === "risk" ? "风险" : "—"
            }
            color={
              item.fundamental_status === "healthy" ? "text-cn-up"
              : item.fundamental_status === "weak" ? "text-yellow-400"
              : item.fundamental_status === "risk" ? "text-cn-dn" : "text-ink-300"
            }
          />
          {item.triggers && item.triggers.length > 0 && (
            <div className="pt-2 border-t border-ink-800/60 mt-2">
              <div className="text-[10px] text-ink-500 mb-1.5">触发条件</div>
              <div className="flex flex-wrap gap-1">
                {item.triggers.map((t, j) => (
                  <span key={j} className="chip chip-on text-[10px]">{t}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Section 3: AI Suggestion ── */}
      <div className="bg-gradient-to-br from-violet-900/20 to-ink-950/60 border border-violet-700/40 rounded-lg p-3 flex flex-col">
        <div className="flex items-center gap-2 mb-2.5 pb-2 border-b border-violet-700/30">
          <i className="fas fa-robot text-violet-300 text-[11px]" />
          <span className="font-semibold text-ink-100">AI 操作建议</span>
        </div>
        <div className="text-[11px] text-ink-400 leading-relaxed mb-3 flex-1">
          跳转到 K 线页调用 AI，会携带：<span className="text-ink-300">全年 K 线概览、MA10/20/50/120/250、TOP10 支撑压力位、基本面/机构共识 + K 线截图</span>，并额外附上本页的<span className="text-violet-300">形态信号、触发条件、量比、盈亏比、关键价位</span>。
        </div>
        <div className="flex flex-col gap-2">
          <button
            disabled={!onAIAnalyze}
            onClick={() => onAIAnalyze && onAIAnalyze(item.code, buildExtra())}
            className="w-full px-3 py-2 text-[12px] rounded-md bg-violet-600/80 hover:bg-violet-600 text-white font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            <i className="fas fa-wand-magic-sparkles mr-1.5" />
            AI 全量分析（含 K 线截图）
          </button>
          <button
            onClick={() => onPickStock(item.code)}
            className="w-full px-3 py-1.5 text-[11px] rounded-md border border-ink-700 hover:border-sky2/50 text-ink-300 hover:text-sky2 transition"
          >
            <i className="fas fa-chart-area mr-1.5" />
            打开 K 线工作区
          </button>
        </div>
        <div className="mt-2 text-[9px] text-ink-600 leading-relaxed">
          ⚠ AI 建议仅供参考，请结合自身判断，谨慎决策。
        </div>
      </div>
    </div>
  );
}

function EvalRow({ label, value, sub, color = "text-ink-200" }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[11px] text-ink-500">{label}</span>
      <span className="flex items-baseline gap-1.5">
        <span className={"num " + color}>{value}</span>
        {sub && <span className="text-[10px] text-ink-500 num">{sub}</span>}
      </span>
    </div>
  );
}
