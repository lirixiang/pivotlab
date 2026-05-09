import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import { TrainPanel } from "../components/TrainPanel";
import { useUrlParam } from "../utils/useUrlParam";
import type {
  Recommendation,
  RecommendListResp,
  RecommendScanProgress,
  RecommendStyle,
} from "../types";

const STYLE_TABS: { key: RecommendStyle | "all"; label: string; desc: string }[] = [
  { key: "all", label: "全部", desc: "所有风格的今日推荐" },
  { key: "short_term", label: "短线打板", desc: "1-5天 · 量价突破+概念热度" },
  { key: "swing", label: "波段交易", desc: "1-4周 · 趋势+回踩支撑" },
  { key: "value", label: "中长线价值", desc: "1-6月 · 基本面+趋势" },
  { key: "multi_factor", label: "量化多因子", desc: "10-30天 · 综合打分" },
  { key: "ai_ensemble", label: "AI 集成", desc: "5-30天 · 规则+LightGBM+TCN 加权" },
];

type SortKey = "rank" | "score" | "price" | "rr" | "confidence" | "position";

type ScreenerHit = { pattern: string; label: string; score: number; scanned_at: string };

export function RecommendPage({
  onPickStock,
  initialCode,
  onClearInitial,
}: {
  onPickStock?: (code: string) => void;
  initialCode?: string;
  onClearInitial?: () => void;
}) {
  const [active, setActive] = useUrlParam<RecommendStyle | "all">("style", "all");
  const [data, setData] = useState<Record<string, RecommendListResp | null>>({});
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState<RecommendScanProgress | null>(null);
  const [selected, setSelected] = useState<Recommendation | null>(null);
  const [sortKey, setSortKey] = useUrlParam<SortKey>("sort", "rank");
  const [sortAsc, setSortAsc] = useState(true);
  const [patterns, setPatterns] = useState<Record<string, ScreenerHit[]>>({});
  const [filterCode, setFilterCode] = useState<string>(initialCode || "");
  const [filterItems, setFilterItems] = useState<Recommendation[] | null>(null);
  const [tierOpen, setTierOpen] = useState<Record<"core" | "watch" | "observe", boolean>>({
    core: true,
    watch: false,
    observe: false,
  });

  // ── Load recommendations for active style (or all) ──
  const loadStyle = useCallback(async (style: RecommendStyle | "all") => {
    setLoading(true);
    try {
      if (style === "all") {
        // Pull each style's top 50 (core + part of watch)
        const styles: RecommendStyle[] = ["short_term", "swing", "value", "multi_factor", "ai_ensemble"];
        const all: Record<string, RecommendListResp> = {};
        for (const s of styles) {
          all[s] = await api.recommendList({ style: s, limit: 50 });
        }
        setData((d) => ({ ...d, ...all }));
      } else {
        const r = await api.recommendList({ style, limit: 100 });
        setData((d) => ({ ...d, [style]: r }));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStyle(active); }, [active, loadStyle]);

  // ── External code filter (from Screener "查看推荐" button) ──
  useEffect(() => {
    setFilterCode(initialCode || "");
  }, [initialCode]);

  useEffect(() => {
    if (!filterCode) { setFilterItems(null); return; }
    let cancelled = false;
    api.recommendDetail(filterCode).then((r) => {
      if (!cancelled) setFilterItems(r.items || []);
    }).catch(() => { if (!cancelled) setFilterItems([]); });
    return () => { cancelled = true; };
  }, [filterCode]);

  // ── Poll in-flight scan ──
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    const tick = async () => {
      try {
        const r = await api.recommendCurrentScan();
        if (r.running) {
          setScanning(r as RecommendScanProgress);
        } else {
          if (scanning && scanning.status !== "done") {
            // Scan just finished — refresh
            await loadStyle(active);
          }
          setScanning(null);
        }
      } catch { /* ignore */ }
    };
    tick();
    timer = setInterval(tick, 3000);
    return () => clearInterval(timer);
  }, [active, loadStyle, scanning?.scan_id]);

  const triggerScan = async () => {
    try {
      await api.recommendScan({ top_n: 300 });
      // Start polling will pick it up
      setTimeout(() => api.recommendCurrentScan().then((r) => {
        if (r.running) setScanning(r as RecommendScanProgress);
      }), 500);
    } catch (e) {
      alert("启动扫描失败: " + (e as Error).message);
    }
  };

  // ── Compose visible items + sort ──
  const items = useMemo<Recommendation[]>(() => {
    let arr: Recommendation[];
    if (filterItems !== null) {
      arr = filterItems;
    } else if (active === "all") {
      arr = (["short_term", "swing", "value", "multi_factor", "ai_ensemble"] as RecommendStyle[])
        .flatMap((s) => data[s]?.items || []);
    } else {
      arr = data[active]?.items || [];
    }
    const dir = sortAsc ? 1 : -1;
    const k = sortKey;
    return [...arr].sort((a, b) => {
      const av = k === "rank" ? (a.rank || 9999)
        : k === "score" ? a.score
        : k === "price" ? a.price
        : k === "rr" ? (a.plan?.risk_reward || 0)
        : k === "confidence" ? (a.plan?.confidence || 0)
        : (a.plan?.position_pct || 0);
      const bv = k === "rank" ? (b.rank || 9999)
        : k === "score" ? b.score
        : k === "price" ? b.price
        : k === "rr" ? (b.plan?.risk_reward || 0)
        : k === "confidence" ? (b.plan?.confidence || 0)
        : (b.plan?.position_pct || 0);
      return (av - bv) * dir;
    });
  }, [active, data, sortKey, sortAsc, filterItems]);

  // ── Bulk-fetch screener patterns for visible codes ──
  useEffect(() => {
    if (items.length === 0) return;
    const codes = Array.from(new Set(items.map((it) => it.code))).filter((c) => !(c in patterns));
    if (codes.length === 0) return;
    let cancelled = false;
    // Limit per-call to ~50 codes
    const chunks: string[][] = [];
    for (let i = 0; i < codes.length; i += 50) chunks.push(codes.slice(i, i + 50));
    Promise.all(chunks.map((ch) => api.screenerByCodes(ch).catch(() => ({} as Record<string, ScreenerHit[]>))))
      .then((results) => {
        if (cancelled) return;
        setPatterns((prev) => {
          const merged = { ...prev };
          for (const r of results) for (const [c, arr] of Object.entries(r)) merged[c] = arr;
          // Codes with no result → mark as empty array so we don't refetch
          for (const c of codes) if (!(c in merged)) merged[c] = [];
          return merged;
        });
      });
    return () => { cancelled = true; };
  }, [items, patterns]);

  const scanDate = data[active === "all" ? "swing" : active]?.scan_date || "—";

  const onSort = (k: SortKey) => {
    if (sortKey === k) setSortAsc((v) => !v);
    else { setSortKey(k); setSortAsc(k === "rank"); }
  };

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* ── Left: list ── */}
      <div className="flex-1 flex flex-col min-w-0 border-r border-ink-800">
        <div className="px-4 py-3 border-b border-ink-800 flex items-center gap-3">
          <div className="flex-1">
            <h1 className="text-lg font-semibold text-white">今日推荐 · 智能选股</h1>
            <div className="text-xs text-ink-500 mt-0.5">
              扫描日期 {scanDate} · 共 {items.length} 只
              {scanning && (
                <span className="ml-3 text-gold">
                  扫描中 {scanning.pct}% ({scanning.phase})
                </span>
              )}
            </div>
          </div>
          <button
            onClick={triggerScan}
            disabled={!!scanning}
            className="px-3 py-1.5 rounded-md bg-gold text-ink-950 font-medium text-sm hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {scanning ? `扫描中 ${scanning.pct}%` : "立即扫描全市场"}
          </button>
        </div>

        {filterCode && (
          <div className="px-4 py-2 bg-gold/10 border-b border-gold/30 flex items-center gap-3 text-xs">
            <i className="fas fa-filter text-gold" />
            <span className="text-ink-200">
              当前查看 <b className="text-gold font-mono">{filterCode}</b> 的推荐
              {filterItems !== null && (
                <span className="ml-2 text-ink-500">(命中 {filterItems.length} 个风格)</span>
              )}
            </span>
            <button
              onClick={() => { setFilterCode(""); onClearInitial?.(); }}
              className="ml-auto px-2 py-0.5 rounded text-ink-300 hover:text-white hover:bg-ink-800"
            >
              <i className="fas fa-times mr-1" />取消过滤
            </button>
          </div>
        )}

        <MarketEnvLifecycleBar />

        {/* Style tabs */}
        <div className="flex border-b border-ink-800 px-4 gap-1 overflow-x-auto">
          {STYLE_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setActive(t.key)}
              className={
                "px-3 py-2 text-sm whitespace-nowrap border-b-2 transition " +
                (active === t.key
                  ? "border-gold text-gold"
                  : "border-transparent text-ink-400 hover:text-white")
              }
              title={t.desc}
            >
              {t.label}
              {data[t.key === "all" ? "swing" : t.key] && (
                <span className="ml-1.5 text-[10px] text-ink-500">
                  {t.key === "all"
                    ? items.length
                    : data[t.key]?.count || 0}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* AI training panel — only on the AI ensemble tab */}
        {active === "ai_ensemble" && <TrainPanel />}

        {/* Table */}
        <div className="flex-1 overflow-auto">
          {loading && <div className="p-8 text-center text-ink-500">加载中…</div>}
          {!loading && items.length === 0 && (
            <div className="p-8 text-center text-ink-500">
              没有推荐数据。点击右上角 <b className="text-gold">「立即扫描全市场」</b> 生成今日推荐。
            </div>
          )}
          {!loading && items.length > 0 && (
            <table className="w-full text-sm">
              <thead className="text-[11px] text-ink-500 uppercase tracking-wider sticky top-0 bg-ink-900 z-10">
                <tr>
                  <Th sortKey="rank" cur={sortKey} asc={sortAsc} onClick={onSort}>#</Th>
                  <th className="text-left px-3 py-2">状态</th>
                  <th className="text-left px-3 py-2">股票</th>
                  <th className="text-left px-3 py-2">形态</th>
                  <th className="text-left px-3 py-2">行业 / 概念</th>
                  <th className="text-left px-3 py-2">风格</th>
                  <Th sortKey="score" cur={sortKey} asc={sortAsc} onClick={onSort}>评分</Th>
                  <Th sortKey="price" cur={sortKey} asc={sortAsc} onClick={onSort}>现价</Th>
                  <th className="text-left px-3 py-2">买入区</th>
                  <th className="text-left px-3 py-2">止损</th>
                  <th className="text-left px-3 py-2">目标</th>
                  <Th sortKey="rr" cur={sortKey} asc={sortAsc} onClick={onSort}>盈亏比</Th>
                  <Th sortKey="position" cur={sortKey} asc={sortAsc} onClick={onSort}>仓位</Th>
                  <Th sortKey="confidence" cur={sortKey} asc={sortAsc} onClick={onSort}>置信</Th>
                </tr>
              </thead>
              <tbody>
                {(["core", "watch", "observe"] as const).map((tier) => {
                  const tierItems = items.filter((it) => {
                    const t = it.tier || (((it.rank ?? 9999) <= 20) ? "core" : ((it.rank ?? 9999) <= 100 ? "watch" : "observe"));
                    return t === tier;
                  });
                  const meta = {
                    core: { label: "⭐ 核心推荐", desc: "Top 20 · 高置信度", color: "text-gold", bg: "bg-gold/10" },
                    watch: { label: "📋 备选池", desc: "21-100 · 中等置信", color: "text-sky-300", bg: "bg-sky-500/10" },
                    observe: { label: "🔭 观察池", desc: "101+ · 量化研究", color: "text-ink-400", bg: "bg-ink-850" },
                  }[tier];
                  const open = tierOpen[tier];
                  const rowEls: JSX.Element[] = [
                    <tr
                      key={`${tier}-hdr`}
                      onClick={() => setTierOpen((s) => ({ ...s, [tier]: !s[tier] }))}
                      className={`border-t border-ink-800 cursor-pointer hover:bg-ink-800/60 ${meta.bg}`}
                    >
                      <td colSpan={14} className="px-3 py-2">
                        <div className="flex items-center gap-3 text-sm">
                          <i className={`fas fa-chevron-${open ? "down" : "right"} text-[10px] ${meta.color}`} />
                          <span className={`font-medium ${meta.color}`}>{meta.label}</span>
                          <span className="text-ink-500 text-xs">{meta.desc}</span>
                          <span className="ml-auto text-ink-400 text-xs">
                            {tierItems.length} 只
                            {!open && tierItems.length > 0 && (
                              <span className="ml-2 text-ink-600">(点击展开)</span>
                            )}
                          </span>
                        </div>
                      </td>
                    </tr>,
                  ];
                  if (open) {
                    tierItems.forEach((it, i) => {
                      rowEls.push(
                        <tr
                          key={`${tier}-${it.code}-${it.style}-${i}`}
                          onClick={() => setSelected(it)}
                          className={
                            "border-t border-ink-800 cursor-pointer hover:bg-ink-850 " +
                            (selected?.code === it.code && selected?.style === it.style
                              ? "bg-ink-850" : "")
                          }
                        >
                    <td className="px-3 py-2 text-ink-500">{it.rank ?? "—"}</td>
                    <td className="px-3 py-2">
                      <StateBadge plan={it.plan} />
                    </td>
                    <td className="px-3 py-2">
                      <div
                        className="font-medium text-white hover:text-gold cursor-pointer inline-flex items-center gap-1"
                        title="点击查看 K 线"
                        onClick={(e) => { e.stopPropagation(); onPickStock?.(it.code); }}
                      >
                        {it.name}
                        <i className="fas fa-chart-line text-[10px] text-ink-500" />
                      </div>
                      <div
                        className="text-[11px] text-ink-500 font-mono hover:text-gold cursor-pointer"
                        title="点击查看 K 线"
                        onClick={(e) => { e.stopPropagation(); onPickStock?.(it.code); }}
                      >
                        {it.code}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {(() => {
                        const hits = patterns[it.code] || [];
                        if (hits.length === 0) return <span className="text-ink-600 text-[11px]">—</span>;
                        const tip = hits.map((h) => `${h.label} (${Math.round(h.score)})`).join("\n");
                        return (
                          <span
                            title={tip}
                            className={
                              "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border " +
                              (hits.length >= 2
                                ? "bg-gold/20 text-gold border-gold/40"
                                : "bg-sky-500/15 text-sky-300 border-sky-500/30")
                            }
                          >
                            <i className="fas fa-bullseye text-[9px]" />
                            {hits.length >= 2 ? `共振×${hits.length}` : hits[0].label}
                          </span>
                        );
                      })()}
                    </td>
                    <td className="px-3 py-2 text-ink-300">
                      <div>{it.industry || "—"}</div>
                      {it.concept && <div className="text-[11px] text-gold/80">{it.concept}</div>}
                    </td>
                    <td className="px-3 py-2 text-xs text-ink-400">{styleLabel(it.style)}</td>
                    <td className="px-3 py-2">
                      <ScoreBar v={it.score} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{it.price.toFixed(2)}</td>
                    <td className="px-3 py-2 font-mono text-cn-up">
                      {it.plan ? `${it.plan.buy_low.toFixed(2)}–${it.plan.buy_high.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-cn-dn">
                      {it.plan ? it.plan.stop_loss.toFixed(2) : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-gold">
                      {it.plan ? `${it.plan.take_profit_1.toFixed(2)} / ${it.plan.take_profit_2.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{it.plan?.risk_reward.toFixed(1) ?? "—"}</td>
                    <td className="px-3 py-2 text-right">
                      {it.plan ? `${(it.plan.position_pct * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className={
                        "px-1.5 py-0.5 rounded text-[11px] " +
                        ((it.plan?.confidence || 0) >= 70 ? "bg-cn-up/20 text-cn-up"
                          : (it.plan?.confidence || 0) >= 50 ? "bg-gold/20 text-gold"
                          : "bg-ink-800 text-ink-400")
                      }>
                        {it.plan?.confidence.toFixed(0) ?? "—"}
                      </span>
                    </td>
                        </tr>
                      );
                    });
                  }
                  return <>{rowEls}</>;
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* ── Right: detail ── */}
      <aside className="w-[420px] flex flex-col bg-ink-900 overflow-hidden">
        {!selected ? (
          <div className="flex-1 flex items-center justify-center text-ink-500 text-sm p-6 text-center">
            选中左侧任意一只股票<br />查看完整交易计划与依据
          </div>
        ) : (
          <DetailPanel item={selected} onJumpToChart={onPickStock} />
        )}
      </aside>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────

function Th({
  children, sortKey, cur, asc, onClick,
}: {
  children: React.ReactNode;
  sortKey: SortKey;
  cur: SortKey;
  asc: boolean;
  onClick: (k: SortKey) => void;
}) {
  return (
    <th
      className="text-right px-3 py-2 cursor-pointer hover:text-white select-none"
      onClick={() => onClick(sortKey)}
    >
      {children}
      {cur === sortKey && <span className="ml-1">{asc ? "▲" : "▼"}</span>}
    </th>
  );
}

function ScoreBar({ v }: { v: number }) {
  const w = Math.max(0, Math.min(100, v));
  const color = v >= 80 ? "bg-cn-up" : v >= 60 ? "bg-gold" : "bg-ink-600";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-ink-800 rounded overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${w}%` }} />
      </div>
      <span className="text-xs font-mono w-8 text-right">{v.toFixed(0)}</span>
    </div>
  );
}

function StateBadge({ plan }: { plan: Recommendation["plan"] }) {
  if (!plan) return <span className="text-ink-500 text-xs">—</span>;
  const state = plan.state ?? (plan.tradable === false ? "wait_breakout" : "buy");
  const cfg: Record<string, { label: string; cls: string }> = {
    buy: { label: "可买入", cls: "bg-cn-up/20 text-cn-up" },
    wait_breakout: { label: "等突破", cls: "bg-gold/20 text-gold" },
    wait_pullback: { label: "等回踩", cls: "bg-blue-500/20 text-blue-400" },
    reject: { label: "不推荐", cls: "bg-cn-dn/20 text-cn-dn" },
  };
  const c = cfg[state] ?? cfg.buy;
  return (
    <span
      title={plan.risk_warning || ""}
      className={`px-1.5 py-0.5 rounded text-[11px] whitespace-nowrap ${c.cls}`}
    >
      {c.label}
    </span>
  );
}

function MarketEnvLifecycleBar() {
  const [env, setEnv] = useState<{ trend: number; atr_pct: number; verdict: string } | null>(null);
  const [stats, setStats] = useState<{
    n: number; completed?: number; win_rate?: number;
    avg_return?: number; by_state: Record<string, number>;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const [e, s] = await Promise.all([
        api.recommendMarketEnv(),
        api.recommendLifecycleStats(undefined, 60),
      ]);
      setEnv({ trend: e.trend, atr_pct: e.atr_pct, verdict: e.verdict });
      setStats(s);
    } catch (err) { console.warn(err); }
  };
  useEffect(() => { refresh(); }, []);

  const runUpdate = async () => {
    setBusy(true);
    try {
      await api.recommendLifecycleUpdate(60);
      await refresh();
    } finally { setBusy(false); }
  };
  const syncIdx = async () => {
    setBusy(true);
    try {
      await api.recommendSyncIndices();
      await refresh();
    } finally { setBusy(false); }
  };

  const trendColor =
    !env ? "text-ink-500" :
    env.trend > 0.5 ? "text-cn-up" :
    env.trend > 0.1 ? "text-gold" :
    env.trend < -0.1 ? "text-cn-dn" : "text-ink-300";

  const wr = stats?.win_rate;
  const wrColor =
    wr === undefined ? "text-ink-500" :
    wr >= 55 ? "text-cn-up" :
    wr >= 45 ? "text-gold" : "text-cn-dn";

  return (
    <div className="px-4 py-2 border-b border-ink-800 bg-ink-925 flex items-center gap-4 text-xs flex-wrap">
      <div className="flex items-center gap-1.5">
        <span className="text-ink-500">大盘</span>
        <span className={`font-semibold ${trendColor}`}>
          {env ? env.verdict : "—"}
        </span>
        {env && (
          <span className="text-ink-500 font-mono">
            (trend {env.trend.toFixed(2)} · ATR {env.atr_pct.toFixed(2)}%)
          </span>
        )}
      </div>

      <div className="h-4 w-px bg-ink-800" />

      <div className="flex items-center gap-1.5">
        <span className="text-ink-500">近60日推荐</span>
        <span className="font-mono text-white">{stats?.n ?? 0}</span>
        <span className="text-ink-500">条 · 已完结</span>
        <span className="font-mono text-white">{stats?.completed ?? 0}</span>
      </div>

      {stats && stats.completed && stats.completed > 0 && (
        <>
          <div className="flex items-center gap-1.5">
            <span className="text-ink-500">胜率</span>
            <span className={`font-mono font-semibold ${wrColor}`}>
              {wr?.toFixed(1)}%
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-ink-500">均收益</span>
            <span className={`font-mono ${(stats.avg_return ?? 0) > 0 ? "text-cn-up" : "text-cn-dn"}`}>
              {(stats.avg_return ?? 0) > 0 ? "+" : ""}
              {(stats.avg_return ?? 0).toFixed(2)}%
            </span>
          </div>
        </>
      )}

      {stats && Object.entries(stats.by_state).length > 0 && (
        <div className="flex items-center gap-1.5 text-[10px]">
          {Object.entries(stats.by_state).map(([k, v]) => (
            <span key={k} className="px-1.5 py-0.5 rounded bg-ink-800 text-ink-300">
              {k} {v}
            </span>
          ))}
        </div>
      )}

      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={syncIdx} disabled={busy}
          className="px-2 py-0.5 rounded bg-ink-800 hover:bg-ink-700 text-ink-200 disabled:opacity-50"
        >
          同步指数
        </button>
        <button
          onClick={runUpdate} disabled={busy}
          className="px-2 py-0.5 rounded bg-ink-800 hover:bg-ink-700 text-ink-200 disabled:opacity-50"
        >
          {busy ? "更新中…" : "刷新生命周期"}
        </button>
      </div>
    </div>
  );
}

function DetailPanel({
  item, onJumpToChart,
}: {
  item: Recommendation;
  onJumpToChart?: (code: string) => void;
}) {
  const p = item.plan;
  return (
    <>
      <div className="p-4 border-b border-ink-800">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-white">{item.name}</div>
            <div className="text-xs font-mono text-ink-500">
              {item.code} · {item.industry || "未分类"}
            </div>
          </div>
          {onJumpToChart && (
            <button
              onClick={() => onJumpToChart(item.code)}
              className="px-2 py-1 text-xs rounded bg-ink-800 hover:bg-ink-700 text-ink-200"
            >
              查看K线 →
            </button>
          )}
        </div>
        <div className="mt-3 flex items-center gap-3 text-sm">
          <div>
            <div className="text-[10px] text-ink-500 uppercase">现价</div>
            <div className="font-mono text-white">{item.price.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-[10px] text-ink-500 uppercase">风格</div>
            <div className="text-gold">{styleLabel(item.style)}</div>
          </div>
          <div>
            <div className="text-[10px] text-ink-500 uppercase">综合评分</div>
            <div className="font-mono">{item.score.toFixed(0)} / 100</div>
          </div>
        </div>
      </div>

      {p && (
        <div className="p-4 border-b border-ink-800 space-y-3">
          <h3 className="text-sm font-semibold text-gold flex items-center gap-2">
            <i className="fas fa-bullseye text-xs" />
            交易计划
            <StateBadge plan={p} />
          </h3>

          {p.risk_warning && (
            <div className="p-2 rounded bg-cn-dn/10 border border-cn-dn/30 text-xs text-cn-dn leading-relaxed">
              ⚠️ {p.risk_warning}
            </div>
          )}

          <PlanRow label="买入区间" value={
            <span className="font-mono text-cn-up">
              {p.buy_low.toFixed(2)} – {p.buy_high.toFixed(2)}
            </span>
          } note={p.buy_trigger} />

          <PlanRow label="止损" value={
            <span className="font-mono text-cn-dn">{p.stop_loss.toFixed(2)}</span>
          } note={`-${(((p.buy_low + p.buy_high) / 2 - p.stop_loss) / ((p.buy_low + p.buy_high) / 2) * 100).toFixed(1)}%`} />

          <PlanRow label="第一目标" value={
            <span className="font-mono text-gold">{p.take_profit_1.toFixed(2)}</span>
          } note="建议减半仓" />

          <PlanRow label="第二目标" value={
            <span className="font-mono text-gold">{p.take_profit_2.toFixed(2)}</span>
          } note="全部止盈" />

          <div className="grid grid-cols-3 gap-2 pt-2">
            <Stat label="盈亏比" v={`${p.risk_reward.toFixed(1)} : 1`} />
            <Stat label="建议仓位" v={`${(p.position_pct * 100).toFixed(0)}%`} />
            <Stat label="持有" v={`${p.holding_days_min}-${p.holding_days_max} 天`} />
            <Stat label="置信度" v={`${p.confidence.toFixed(0)}%`} />
            <Stat label="ATR" v={`${p.atr_pct.toFixed(1)}%`} />
            <Stat label="评分" v={item.score.toFixed(0)} />
          </div>
        </div>
      )}

      <div className="p-4 flex-1 overflow-auto">
        <h3 className="text-sm font-semibold text-ink-200 mb-2">入选理由</h3>
        <ul className="space-y-1.5 text-sm text-ink-300">
          {item.reasons.map((r, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-gold mt-0.5">·</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
        {p?.reason && (
          <div className="mt-4 p-3 rounded bg-ink-850 text-xs text-ink-400 leading-relaxed">
            {p.reason}
          </div>
        )}
      </div>
    </>
  );
}

function PlanRow({
  label, value, note,
}: { label: string; value: React.ReactNode; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-xs text-ink-500 w-20">{label}</span>
      <div className="flex-1 text-right">
        <div className="text-base">{value}</div>
        {note && <div className="text-[10px] text-ink-500 mt-0.5">{note}</div>}
      </div>
    </div>
  );
}

function Stat({ label, v }: { label: string; v: string | number }) {
  return (
    <div className="bg-ink-850 rounded px-2 py-1.5">
      <div className="text-[10px] text-ink-500">{label}</div>
      <div className="text-sm font-mono text-white">{v}</div>
    </div>
  );
}

function styleLabel(s: string): string {
  return ({
    short_term: "短线打板",
    swing: "波段交易",
    value: "中长线",
    multi_factor: "多因子",
  } as Record<string, string>)[s] || s;
}
