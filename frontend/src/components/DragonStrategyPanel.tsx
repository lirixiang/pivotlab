import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type {
  BoardHeatItem,
  DragonScanCandidate,
  DragonScanJob,
  DragonStatus,
  DragonTrainJob,
  MarketCycle,
  ZtPoolItem,
} from "../types";

type SubTab = "today" | "boards" | "ztpool" | "train" | "backtest" | "knowledge";

const PHASE_LABEL: Record<string, { label: string; color: string; icon: string; tip: string }> = {
  ice:      { label: "冰点期", color: "text-cyan-400 bg-cyan-900/30 ring-cyan-700",   icon: "fa-snowflake",  tip: "涨停<30, 不参与" },
  warmup:   { label: "回暖期", color: "text-amber-400 bg-amber-900/30 ring-amber-700", icon: "fa-fire",       tip: "试探性参与, 仓位<30%" },
  peak:     { label: "高潮期", color: "text-red-400 bg-red-900/30 ring-red-700",       icon: "fa-fire-flame-curved", tip: "追龙头/做补涨" },
  cooldown: { label: "退潮期", color: "text-purple-400 bg-purple-900/30 ring-purple-700", icon: "fa-cloud-sun-rain", tip: "只卖不买" },
};

function PhaseBadge({ phase }: { phase: string }) {
  const p = PHASE_LABEL[phase] || PHASE_LABEL.warmup;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs ring-1 ${p.color}`}>
      <i className={`fas ${p.icon}`} />
      {p.label}
    </span>
  );
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(v as number)) return "—";
  return Number(v).toFixed(digits);
}

function fmtAmt(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return String(v);
}

export function DragonStrategyPanel({ defaultCode }: { defaultCode: string }) {
  const [sub, setSub] = useState<SubTab>("today");
  const [status, setStatus] = useState<DragonStatus | null>(null);
  const [cycle, setCycle] = useState<MarketCycle | null>(null);

  const refreshHeader = useCallback(() => {
    api.dragonStatus().then(setStatus).catch(() => {});
    api.dragonMarketCycle().then(setCycle).catch(() => {});
  }, []);

  useEffect(() => { refreshHeader(); }, [refreshHeader]);

  const subs: { k: SubTab; l: string; icon: string }[] = [
    { k: "today",     l: "今日龙头",   icon: "fa-dragon" },
    { k: "boards",    l: "主线板块",   icon: "fa-layer-group" },
    { k: "ztpool",    l: "涨停池",     icon: "fa-arrow-up-right-dots" },
    { k: "train",     l: "模型训练",   icon: "fa-graduation-cap" },
    { k: "backtest",  l: "策略回测",   icon: "fa-chart-line" },
    { k: "knowledge", l: "游资经验",   icon: "fa-book" },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Sub-header: market thermometer */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-ink-800 bg-ink-900/30">
        <div className="flex items-center gap-2">
          <i className="fas fa-dragon text-amber-400" />
          <span className="text-sm font-semibold text-white">龙头战法</span>
        </div>
        {cycle && (
          <>
            <PhaseBadge phase={cycle.phase} />
            <div className="text-xs text-ink-300">
              情绪 <span className="text-white font-bold">{fmt(cycle.score, 0)}</span>
              <span className="text-ink-600">/100</span>
            </div>
            <div className="text-xs text-ink-300">
              涨停 <span className="text-red-400 font-mono">{cycle.zt_count}</span>
              <span className="text-ink-600 mx-1">·</span>
              炸板 <span className="text-purple-400 font-mono">{cycle.blast_count}</span>
              <span className="text-ink-600 mx-1">·</span>
              炸板率 <span className="font-mono">{(cycle.blast_rate * 100).toFixed(1)}%</span>
            </div>
            <div className="text-xs text-ink-300">
              最高 <span className="text-amber-400 font-bold">{cycle.high_consecutive}板</span>
              <span className="text-ink-600 mx-1">·</span>
              3板+ <span className="font-mono">{cycle.consecutive_3plus}</span>
            </div>
            <div className="text-xs text-ink-300">
              昨涨停今表现{" "}
              <span className={cycle.yesterday_zt_today_perf >= 0 ? "text-red-400 font-mono" : "text-green-400 font-mono"}>
                {cycle.yesterday_zt_today_perf > 0 ? "+" : ""}{fmt(cycle.yesterday_zt_today_perf, 2)}%
              </span>
            </div>
          </>
        )}
        <div className="ml-auto flex items-center gap-2 text-[11px]">
          {status && (
            <>
              <span className={`flex items-center gap-1 px-2 py-1 rounded ring-1 ${status.stage1.trained ? "ring-green-700 text-green-400" : "ring-ink-700 text-ink-500"}`}>
                <i className="fas fa-circle text-[6px]" /> S1 {status.stage1.trained ? "已训练" : "未训练"}
              </span>
              <span className={`flex items-center gap-1 px-2 py-1 rounded ring-1 ${status.stage2.trained ? "ring-purple-700 text-purple-400" : "ring-ink-700 text-ink-500"}`}>
                <i className="fas fa-circle text-[6px]" /> S2 {status.stage2.trained ? "已训练" : "未训练"}
              </span>
            </>
          )}
          <button onClick={refreshHeader}
            className="px-2 py-1 rounded text-ink-500 hover:text-ink-200 hover:bg-ink-800">
            <i className="fas fa-rotate" />
          </button>
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="flex items-center gap-1 px-5 py-2 border-b border-ink-800">
        {subs.map((t) => (
          <button key={t.k} onClick={() => setSub(t.k)}
            className={
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition " +
              (sub === t.k ? "bg-ink-800 text-white ring-1 ring-amber-700/50" : "text-ink-500 hover:text-ink-200")
            }>
            <i className={`fas ${t.icon} text-[10px]`} />
            {t.l}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {sub === "today" && <TodayDragonsPanel cycle={cycle} />}
        {sub === "boards" && <BoardsPanel />}
        {sub === "ztpool" && <ZtPoolPanel />}
        {sub === "train" && <DragonTrainPanel onTrained={refreshHeader} />}
        {sub === "backtest" && <DragonBacktestPanel />}
        {sub === "knowledge" && <KnowledgePanel />}
      </div>
    </div>
  );
}


/* ─────────────── Today Dragons (live scan) ─────────────── */

function TodayDragonsPanel({ cycle }: { cycle: MarketCycle | null }) {
  const [job, setJob] = useState<DragonScanJob | null>(null);
  const [running, setRunning] = useState(false);
  const [threshold, setThreshold] = useState(60);
  const [topN, setTopN] = useState(20);
  const [sortKey, setSortKey] = useState<keyof DragonScanCandidate>("dragon_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const startScan = useCallback(async () => {
    setRunning(true);
    try {
      const r = await api.dragonScan({ threshold, top_n: topN, persist: true });
      // Poll progress
      const tid = r.task_id;
      const poll = setInterval(async () => {
        const jobs = await api.dragonScanProgress();
        const j = jobs.find((x) => x.task_id === tid);
        if (j) {
          setJob(j);
          if (j.status !== "running") {
            clearInterval(poll);
            setRunning(false);
          }
        }
      }, 1000);
    } catch (e) {
      setRunning(false);
    }
  }, [threshold, topN]);

  // On mount: try to load latest persisted signals
  useEffect(() => {
    api.dragonTodaySignals().then((r) => {
      if (r.items.length > 0 && !job) {
        // Build a synthetic job for display
        const cands: DragonScanCandidate[] = r.items.map((it) => ({
          code: it.code, name: it.name, trade_date: r.trade_date,
          signal_type: (it.signal_type as "buy" | "sell" | "hold") || "hold",
          dragon_score: it.dragon_score, dragon_rank: it.dragon_rank,
          consecutive: it.consecutive, concept: it.concept,
          market_cycle: it.market_cycle, model_confidence: it.model_conf,
          entry_price: it.entry_price, stop_price: it.stop_price,
          target_price: it.target_price, reasons: [], feature_snapshot: {},
        }));
        setJob({
          task_id: "loaded", status: "done", progress: 100,
          message: `已加载 ${cands.length} 个持久化信号`,
          date: r.trade_date, threshold: 0,
          started_at: 0, ended_at: 0, candidates: cands,
        });
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sorted = useMemo(() => {
    if (!job) return [];
    const arr = [...job.candidates];
    arr.sort((a, b) => {
      const av = (a[sortKey] ?? 0) as number;
      const bv = (b[sortKey] ?? 0) as number;
      const cmp = typeof av === "string" ? String(av).localeCompare(String(bv)) : av - bv;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [job, sortKey, sortDir]);

  const onSort = (k: keyof DragonScanCandidate) => {
    if (sortKey === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir("desc"); }
  };

  const sortIcon = (k: keyof DragonScanCandidate) => {
    if (sortKey !== k) return <i className="fas fa-sort text-ink-700 ml-1" />;
    return <i className={`fas fa-sort-${sortDir === "asc" ? "up" : "down"} text-amber-400 ml-1`} />;
  };

  const isRiskyPhase = cycle && (cycle.phase === "ice" || cycle.phase === "cooldown");

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={startScan} disabled={running}
          className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-sm font-medium">
          <i className="fas fa-radar mr-2" />
          {running ? "扫描中..." : "扫描今日龙头"}
        </button>
        <div className="flex items-center gap-2 text-xs text-ink-400">
          <span>龙头分阈值</span>
          <input type="number" value={threshold} onChange={(e) => setThreshold(+e.target.value)}
            className="w-16 px-2 py-1 bg-ink-900 rounded ring-1 ring-ink-700 text-white" />
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-400">
          <span>Top N</span>
          <input type="number" value={topN} onChange={(e) => setTopN(+e.target.value)}
            className="w-16 px-2 py-1 bg-ink-900 rounded ring-1 ring-ink-700 text-white" />
        </div>
        {isRiskyPhase && (
          <span className="text-xs text-amber-400 ml-2">
            <i className="fas fa-triangle-exclamation mr-1" />
            当前为{PHASE_LABEL[cycle!.phase].label}，建议{cycle!.phase === "ice" ? "停止参与" : "只减不加"}
          </span>
        )}
      </div>

      {job && (
        <div className="text-xs text-ink-500 flex items-center gap-3">
          <span>{job.message}</span>
          {job.status === "running" && (
            <div className="flex-1 h-1.5 bg-ink-800 rounded overflow-hidden">
              <div className="h-full bg-amber-500 transition-all" style={{ width: `${job.progress}%` }} />
            </div>
          )}
        </div>
      )}

      {sorted.length > 0 && (
        <div className="rounded-lg ring-1 ring-ink-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-ink-900/60 text-ink-400">
              <tr>
                <th className="px-2 py-2 text-left cursor-pointer" onClick={() => onSort("dragon_rank")}>排名{sortIcon("dragon_rank")}</th>
                <th className="px-2 py-2 text-left">代码</th>
                <th className="px-2 py-2 text-left">名称</th>
                <th className="px-2 py-2 text-right cursor-pointer" onClick={() => onSort("consecutive")}>连板{sortIcon("consecutive")}</th>
                <th className="px-2 py-2 text-right cursor-pointer" onClick={() => onSort("dragon_score")}>龙头分{sortIcon("dragon_score")}</th>
                <th className="px-2 py-2 text-left">主题</th>
                <th className="px-2 py-2 text-center">信号</th>
                <th className="px-2 py-2 text-right cursor-pointer" onClick={() => onSort("model_confidence")}>置信度{sortIcon("model_confidence")}</th>
                <th className="px-2 py-2 text-right">入场</th>
                <th className="px-2 py-2 text-right">止损</th>
                <th className="px-2 py-2 text-right">目标</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((c) => <DragonRow key={c.code} c={c} />)}
            </tbody>
          </table>
        </div>
      )}

      {sorted.length === 0 && !running && (
        <div className="p-10 text-center text-ink-500 text-sm">
          <i className="fas fa-dragon text-3xl text-ink-700 mb-3 block" />
          点击「扫描今日龙头」开始，或先在「模型训练」中训练龙头识别模型
        </div>
      )}
    </div>
  );
}

function DragonRow({ c }: { c: DragonScanCandidate }) {
  const [open, setOpen] = useState(false);
  const sigColor = c.signal_type === "buy" ? "text-red-400 bg-red-900/30 ring-red-700"
    : c.signal_type === "sell" ? "text-green-400 bg-green-900/30 ring-green-700"
    : "text-ink-400 bg-ink-800 ring-ink-700";
  const sigLabel = c.signal_type === "buy" ? "买入" : c.signal_type === "sell" ? "卖出" : "持有";
  const scoreColor = c.dragon_score >= 80 ? "text-red-400" : c.dragon_score >= 60 ? "text-amber-400" : "text-ink-400";

  return (
    <>
      <tr className="border-t border-ink-800 hover:bg-ink-900/40 cursor-pointer" onClick={() => setOpen(!open)}>
        <td className="px-2 py-1.5 font-mono">#{c.dragon_rank}</td>
        <td className="px-2 py-1.5 font-mono text-amber-300">{c.code}</td>
        <td className="px-2 py-1.5 text-white">{c.name}</td>
        <td className="px-2 py-1.5 text-right">
          {c.consecutive >= 2
            ? <span className="px-1.5 py-0.5 rounded bg-red-900/40 text-red-300 font-bold">{c.consecutive}板</span>
            : <span className="text-ink-500">{c.consecutive || "-"}</span>}
        </td>
        <td className={`px-2 py-1.5 text-right font-mono font-bold ${scoreColor}`}>{c.dragon_score.toFixed(1)}</td>
        <td className="px-2 py-1.5 text-ink-300 truncate max-w-[12rem]" title={c.concept}>{c.concept || "—"}</td>
        <td className="px-2 py-1.5 text-center">
          <span className={`px-2 py-0.5 rounded text-[11px] ring-1 ${sigColor}`}>{sigLabel}</span>
        </td>
        <td className="px-2 py-1.5 text-right font-mono">{(c.model_confidence * 100).toFixed(0)}%</td>
        <td className="px-2 py-1.5 text-right font-mono text-ink-300">{fmt(c.entry_price)}</td>
        <td className="px-2 py-1.5 text-right font-mono text-green-400">{fmt(c.stop_price)}</td>
        <td className="px-2 py-1.5 text-right font-mono text-red-400">{fmt(c.target_price)}</td>
      </tr>
      {open && c.reasons.length > 0 && (
        <tr className="bg-ink-950">
          <td colSpan={11} className="px-4 py-3">
            <div className="text-[11px] text-ink-400 space-y-1">
              <div className="text-ink-500 mb-1">评分明细:</div>
              {c.reasons.map((r, i) => <div key={i}>• {r}</div>)}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}


/* ─────────────── Boards Panel ─────────────── */

function BoardsPanel() {
  const [items, setItems] = useState<BoardHeatItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api.dragonBoardHeat({ limit: 30, history_days: 7 })
      .then((r) => setItems(r.items))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const heatColor = (lvl: string) =>
    lvl === "core" ? "text-red-400 bg-red-900/30"
      : lvl === "hot" ? "text-amber-400 bg-amber-900/30"
      : lvl === "watch" ? "text-cyan-400 bg-cyan-900/30"
      : "text-ink-500";

  return (
    <div className="p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm text-ink-300">
          <i className="fas fa-layer-group mr-2 text-amber-400" />
          当日主线板块 · 按热度排序
        </div>
        <button onClick={load} className="text-xs text-ink-400 hover:text-white">
          <i className="fas fa-rotate mr-1" />刷新
        </button>
      </div>
      <div className="rounded-lg ring-1 ring-ink-800 overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-ink-900/60 text-ink-400">
            <tr>
              <th className="px-2 py-2 text-left">排名</th>
              <th className="px-2 py-2 text-left">板块</th>
              <th className="px-2 py-2 text-right">涨幅</th>
              <th className="px-2 py-2 text-right">热度</th>
              <th className="px-2 py-2 text-center">级别</th>
              <th className="px-2 py-2 text-right">涨停</th>
              <th className="px-2 py-2 text-right">上涨比</th>
              <th className="px-2 py-2 text-left">龙头</th>
              <th className="px-2 py-2 text-right">龙头涨幅</th>
              <th className="px-2 py-2 text-left">7日趋势</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={it.concept} className="border-t border-ink-800 hover:bg-ink-900/40">
                <td className="px-2 py-1.5 font-mono">#{i + 1}</td>
                <td className="px-2 py-1.5 text-white">{it.concept}</td>
                <td className="px-2 py-1.5 text-right font-mono">
                  <span className={(it.change_pct ?? 0) >= 0 ? "text-red-400" : "text-green-400"}>
                    {(it.change_pct ?? 0) >= 0 ? "+" : ""}{fmt(it.change_pct, 2)}%
                  </span>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-amber-400 font-bold">{fmt(it.heat_score, 0)}</td>
                <td className="px-2 py-1.5 text-center">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${heatColor(it.heat_level)}`}>{it.heat_level}</span>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-red-300">{it.zt_count}</td>
                <td className="px-2 py-1.5 text-right font-mono">{((it.up_ratio ?? 0) * 100).toFixed(0)}%</td>
                <td className="px-2 py-1.5">
                  {it.leader_code && (
                    <span className="text-amber-300 font-mono text-[11px]">
                      {it.leader_code} {it.leader_name}
                      {it.leader_consecutive >= 2 && (
                        <span className="ml-1 px-1 rounded bg-red-900/40 text-red-300">{it.leader_consecutive}板</span>
                      )}
                    </span>
                  )}
                </td>
                <td className="px-2 py-1.5 text-right font-mono">
                  <span className={(it.leader_change ?? 0) >= 0 ? "text-red-400" : "text-green-400"}>
                    {(it.leader_change ?? 0) >= 0 ? "+" : ""}{fmt(it.leader_change, 2)}%
                  </span>
                </td>
                <td className="px-2 py-1.5">
                  <Sparkline data={it.trend.map((t) => t.score ?? 0)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && items.length === 0 && (
          <div className="p-8 text-center text-ink-500 text-sm">
            暂无数据 · 请先同步「板块热度快照」 (设置→同步任务)
          </div>
        )}
      </div>
    </div>
  );
}

function Sparkline({ data, w = 80, h = 22 }: { data: number[]; w?: number; h?: number }) {
  if (!data || data.length < 2) return <span className="text-ink-700">—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`).join(" ");
  const last = data[data.length - 1];
  const first = data[0];
  const color = last >= first ? "stroke-red-400" : "stroke-green-400";
  return (
    <svg width={w} height={h} className="inline-block">
      <polyline fill="none" strokeWidth="1.5" className={color} points={pts} />
    </svg>
  );
}


/* ─────────────── ZT Pool Panel ─────────────── */

function ZtPoolPanel() {
  const [items, setItems] = useState<ZtPoolItem[]>([]);
  const [poolType, setPoolType] = useState<"zt" | "zb">("zt");
  const [minCons, setMinCons] = useState(1);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillDays, setBackfillDays] = useState(60);

  const load = useCallback(() => {
    setLoading(true);
    api.dragonZtPool({ pool_type: poolType, min_consecutive: minCons, limit: 200 })
      .then((r) => setItems(r.items))
      .finally(() => setLoading(false));
  }, [poolType, minCons]);

  useEffect(() => { load(); }, [load]);

  const triggerSync = async () => {
    setSyncing(true);
    try { await api.dragonSync("zt_pool"); }
    finally { setSyncing(false); setTimeout(load, 1500); }
  };

  const triggerBackfill = async () => {
    if (!confirm(`将回填最近 ${backfillDays} 个日历日的涨停池+龙虎榜+板块热度数据，可能耗时数分钟。继续？`)) return;
    setBackfilling(true);
    try {
      const r = await api.dragonBackfill({ days: backfillDays });
      alert(r.status === "started" ? `已启动后台回填任务 (${backfillDays} 天)` : "已有回填任务正在运行");
    } finally {
      setBackfilling(false);
    }
  };

  return (
    <div className="p-5 space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1 text-xs">
          <button onClick={() => setPoolType("zt")} className={`px-3 py-1 rounded ${poolType === "zt" ? "bg-red-900/40 text-red-300 ring-1 ring-red-700" : "text-ink-500"}`}>
            涨停池
          </button>
          <button onClick={() => setPoolType("zb")} className={`px-3 py-1 rounded ${poolType === "zb" ? "bg-purple-900/40 text-purple-300 ring-1 ring-purple-700" : "text-ink-500"}`}>
            炸板池
          </button>
        </div>
        {poolType === "zt" && (
          <div className="flex items-center gap-2 text-xs text-ink-400">
            <span>最少连板</span>
            <select value={minCons} onChange={(e) => setMinCons(+e.target.value)}
              className="px-2 py-1 bg-ink-900 rounded ring-1 ring-ink-700 text-white">
              <option value={1}>全部</option>
              <option value={2}>2板+</option>
              <option value={3}>3板+</option>
              <option value={5}>5板+</option>
            </select>
          </div>
        )}
        <button onClick={triggerSync} disabled={syncing} className="ml-auto text-xs px-3 py-1.5 rounded bg-ink-800 hover:bg-ink-700 text-ink-200">
          <i className={`fas ${syncing ? "fa-spinner fa-spin" : "fa-cloud-arrow-down"} mr-1`} />
          {syncing ? "同步中" : "同步今日数据"}
        </button>
        <div className="flex items-center gap-1">
          <select value={backfillDays} onChange={(e) => setBackfillDays(+e.target.value)}
            className="px-2 py-1 text-xs bg-ink-900 rounded ring-1 ring-ink-700 text-white">
            <option value={30}>30天</option>
            <option value={60}>60天</option>
            <option value={120}>120天</option>
            <option value={250}>250天</option>
          </select>
          <button onClick={triggerBackfill} disabled={backfilling} className="text-xs px-3 py-1.5 rounded bg-amber-900/40 hover:bg-amber-800/60 text-amber-200 ring-1 ring-amber-700/50">
            <i className={`fas ${backfilling ? "fa-spinner fa-spin" : "fa-clock-rotate-left"} mr-1`} />
            {backfilling ? "提交中" : "回填历史"}
          </button>
        </div>
        <button onClick={load} className="text-xs text-ink-400 hover:text-white">
          <i className="fas fa-rotate mr-1" />刷新
        </button>
      </div>
      <div className="rounded-lg ring-1 ring-ink-800 overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-ink-900/60 text-ink-400">
            <tr>
              <th className="px-2 py-2 text-left">代码</th>
              <th className="px-2 py-2 text-left">名称</th>
              <th className="px-2 py-2 text-right">涨幅</th>
              <th className="px-2 py-2 text-right">价格</th>
              <th className="px-2 py-2 text-right">连板</th>
              <th className="px-2 py-2 text-right">成交额</th>
              <th className="px-2 py-2 text-right">封单</th>
              <th className="px-2 py-2 text-right">换手</th>
              <th className="px-2 py-2 text-center">首封</th>
              <th className="px-2 py-2 text-center">开板</th>
              <th className="px-2 py-2 text-left">题材</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.code} className="border-t border-ink-800 hover:bg-ink-900/40">
                <td className="px-2 py-1.5 font-mono text-amber-300">{r.code}</td>
                <td className="px-2 py-1.5 text-white">{r.name}</td>
                <td className="px-2 py-1.5 text-right font-mono text-red-400">+{fmt(r.change_pct, 2)}%</td>
                <td className="px-2 py-1.5 text-right font-mono">{fmt(r.close, 2)}</td>
                <td className="px-2 py-1.5 text-right">
                  {r.consecutive >= 2
                    ? <span className="px-1.5 py-0.5 rounded bg-red-900/40 text-red-300 font-bold">{r.consecutive}</span>
                    : <span className="text-ink-500">{r.consecutive}</span>}
                </td>
                <td className="px-2 py-1.5 text-right font-mono">{fmtAmt(r.amount)}</td>
                <td className="px-2 py-1.5 text-right font-mono">{fmtAmt(r.seal_amount)}</td>
                <td className="px-2 py-1.5 text-right font-mono">{fmt(r.turnover_rate, 1)}%</td>
                <td className="px-2 py-1.5 text-center font-mono text-ink-400">{r.first_zt_time || "—"}</td>
                <td className="px-2 py-1.5 text-center">{r.open_count > 0 ? <span className="text-amber-400">{r.open_count}</span> : "—"}</td>
                <td className="px-2 py-1.5 text-ink-300 truncate max-w-[10rem]" title={r.concept}>{r.concept}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && items.length === 0 && (
          <div className="p-8 text-center text-ink-500 text-sm">
            暂无数据，点击「同步今日数据」获取
          </div>
        )}
      </div>
    </div>
  );
}


/* ─────────────── Train Panel ─────────────── */

function DragonTrainPanel({ onTrained }: { onTrained: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const sixtyDaysAgo = new Date(Date.now() - 90 * 86400_000).toISOString().slice(0, 10);
  const [start, setStart] = useState(sixtyDaysAgo);
  const [end, setEnd] = useState(today);
  const [epochs, setEpochs] = useState(30);
  const [trainS2, setTrainS2] = useState(true);
  const [jobs, setJobs] = useState<DragonTrainJob[]>([]);

  const refresh = useCallback(() => {
    api.dragonTrainProgress().then(setJobs).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [refresh]);

  const start_ = async () => {
    await api.dragonTrain({ start_date: start, end_date: end, epochs, train_stage2: trainS2 });
    setTimeout(refresh, 500);
    setTimeout(onTrained, 5000);
  };

  return (
    <div className="p-5 space-y-4">
      <div className="rounded-lg ring-1 ring-ink-800 p-4 bg-ink-900/40 space-y-3">
        <div className="text-sm text-ink-300 font-semibold">
          <i className="fas fa-graduation-cap mr-2 text-amber-400" />
          训练龙头模型
        </div>
        <div className="grid grid-cols-4 gap-3 text-xs">
          <div>
            <label className="block text-ink-500 mb-1">开始日期</label>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" />
          </div>
          <div>
            <label className="block text-ink-500 mb-1">结束日期</label>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" />
          </div>
          <div>
            <label className="block text-ink-500 mb-1">训练轮数 (Stage 2)</label>
            <input type="number" value={epochs} onChange={(e) => setEpochs(+e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" />
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-ink-300 cursor-pointer">
              <input type="checkbox" checked={trainS2} onChange={(e) => setTrainS2(e.target.checked)} />
              训练买卖时机模型 (Stage 2)
            </label>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={start_}
            className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium">
            <i className="fas fa-play mr-2" />
            开始训练
          </button>
          <span className="text-[11px] text-ink-500">
            Stage 1: LightGBM 龙头识别 · Stage 2: Transformer 买卖时机
          </span>
        </div>
      </div>

      {/* Job list */}
      {jobs.length > 0 && (
        <div className="rounded-lg ring-1 ring-ink-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-ink-900/60 text-ink-400">
              <tr>
                <th className="px-2 py-2 text-left">任务ID</th>
                <th className="px-2 py-2 text-left">日期范围</th>
                <th className="px-2 py-2 text-center">状态</th>
                <th className="px-2 py-2 text-left">进度</th>
                <th className="px-2 py-2 text-left">结果</th>
                <th className="px-2 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.task_id} className="border-t border-ink-800">
                  <td className="px-2 py-2 font-mono text-ink-400">{j.task_id}</td>
                  <td className="px-2 py-2 text-ink-300">{j.start_date} → {j.end_date}</td>
                  <td className="px-2 py-2 text-center">
                    <span className={
                      "px-2 py-0.5 rounded text-[10px] " +
                      (j.status === "done" ? "bg-green-900/40 text-green-400"
                        : j.status === "error" ? "bg-red-900/40 text-red-400"
                        : "bg-amber-900/40 text-amber-400")
                    }>{j.status}</span>
                  </td>
                  <td className="px-2 py-2">
                    <div className="flex items-center gap-2">
                      <div className="w-24 h-1.5 bg-ink-800 rounded overflow-hidden">
                        <div className="h-full bg-amber-500" style={{ width: `${j.progress}%` }} />
                      </div>
                      <span className="text-[10px] text-ink-500">{j.progress}%</span>
                    </div>
                    <div className="text-[10px] text-ink-500 mt-1 truncate max-w-[24rem]">{j.message}</div>
                  </td>
                  <td className="px-2 py-2 text-[11px]">
                    {j.result ? <ResultSummary result={j.result} /> : "—"}
                  </td>
                  <td className="px-2 py-2">
                    <button onClick={() => api.dragonTrainClear(j.task_id).then(refresh)}
                      className="text-ink-600 hover:text-red-400 text-[11px]">
                      <i className="fas fa-trash" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ResultSummary({ result }: { result: Record<string, unknown> }) {
  const s1 = result.stage1 as { auc?: number; accuracy?: number; samples?: number; dragon_precision?: number; error?: string } | null;
  const s2 = result.stage2 as { accuracy?: number; samples?: number; error?: string } | null;
  return (
    <div className="space-y-1 text-ink-300">
      {s1 && !s1.error && (
        <div>S1: AUC <span className="text-amber-400 font-mono">{s1.auc?.toFixed(3)}</span> · 精度 <span className="font-mono">{(s1.dragon_precision ?? 0).toFixed(2)}</span> · {s1.samples}样本</div>
      )}
      {s1?.error && <div className="text-red-400">S1: {s1.error}</div>}
      {s2 && !s2.error && (
        <div>S2: 准确率 <span className="text-purple-400 font-mono">{((s2.accuracy ?? 0) * 100).toFixed(1)}%</span> · {s2.samples}序列</div>
      )}
      {s2?.error && <div className="text-red-400">S2: {s2.error}</div>}
    </div>
  );
}


/* ─────────────── Backtest Panel ─────────────── */

function DragonBacktestPanel() {
  const today = new Date().toISOString().slice(0, 10);
  const sixtyDaysAgo = new Date(Date.now() - 60 * 86400_000).toISOString().slice(0, 10);
  const [start, setStart] = useState(sixtyDaysAgo);
  const [end, setEnd] = useState(today);
  const [threshold, setThreshold] = useState(70);
  const [holdDays, setHoldDays] = useState(5);
  const [stopPct, setStopPct] = useState(-5);
  const [maxPos, setMaxPos] = useState(3);
  const [filterIce, setFilterIce] = useState(true);
  const [filterCool, setFilterCool] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<import("../types").DragonBacktestResult | null>(null);

  const run = async () => {
    setRunning(true);
    try {
      const r = await api.dragonBacktest({
        start_date: start, end_date: end,
        score_threshold: threshold, hold_days: holdDays,
        stop_pct: stopPct, max_positions: maxPos,
        filter_ice: filterIce, filter_cooldown: filterCool,
      });
      setResult(r);
    } finally { setRunning(false); }
  };

  return (
    <div className="p-5 space-y-4">
      <div className="rounded-lg ring-1 ring-ink-800 p-4 bg-ink-900/40 space-y-3">
        <div className="text-sm text-ink-300 font-semibold">
          <i className="fas fa-chart-line mr-2 text-amber-400" />
          龙头策略回测
        </div>
        <div className="grid grid-cols-4 gap-3 text-xs">
          <div><label className="block text-ink-500 mb-1">开始</label>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" /></div>
          <div><label className="block text-ink-500 mb-1">结束</label>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" /></div>
          <div><label className="block text-ink-500 mb-1">龙头分阈值</label>
            <input type="number" value={threshold} onChange={(e) => setThreshold(+e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" /></div>
          <div><label className="block text-ink-500 mb-1">最大持仓数</label>
            <input type="number" value={maxPos} onChange={(e) => setMaxPos(+e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" /></div>
          <div><label className="block text-ink-500 mb-1">持有天数</label>
            <input type="number" value={holdDays} onChange={(e) => setHoldDays(+e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" /></div>
          <div><label className="block text-ink-500 mb-1">止损 %</label>
            <input type="number" value={stopPct} onChange={(e) => setStopPct(+e.target.value)}
              className="w-full px-2 py-1.5 bg-ink-950 rounded ring-1 ring-ink-700 text-white" /></div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-ink-300 cursor-pointer">
              <input type="checkbox" checked={filterIce} onChange={(e) => setFilterIce(e.target.checked)} />
              冰点期不开仓
            </label>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-ink-300 cursor-pointer">
              <input type="checkbox" checked={filterCool} onChange={(e) => setFilterCool(e.target.checked)} />
              退潮期不开仓
            </label>
          </div>
        </div>
        <button onClick={run} disabled={running}
          className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-sm">
          <i className="fas fa-play mr-2" />
          {running ? "回测中..." : "运行回测"}
        </button>
      </div>

      {result && !result.error && (
        <div className="space-y-3">
          <div className="grid grid-cols-5 gap-3">
            <Stat label="总收益" value={`${result.total_return_pct.toFixed(2)}%`}
              color={result.total_return_pct >= 0 ? "text-red-400" : "text-green-400"} />
            <Stat label="最大回撤" value={`${result.max_drawdown_pct.toFixed(2)}%`} color="text-purple-400" />
            <Stat label="胜率" value={`${(result.win_rate * 100).toFixed(1)}%`} color="text-amber-400" />
            <Stat label="交易次数" value={String(result.trades)} color="text-cyan-400" />
            <Stat label="平均盈亏" value={`+${result.avg_win_pct.toFixed(1)}% / ${result.avg_loss_pct.toFixed(1)}%`} color="text-ink-300" />
          </div>
          {result.trade_list.length > 0 && (
            <div className="rounded-lg ring-1 ring-ink-800 overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-ink-900/60 text-ink-400">
                  <tr>
                    <th className="px-2 py-2 text-left">代码</th>
                    <th className="px-2 py-2 text-left">入场日</th>
                    <th className="px-2 py-2 text-left">出场日</th>
                    <th className="px-2 py-2 text-right">入场</th>
                    <th className="px-2 py-2 text-right">出场</th>
                    <th className="px-2 py-2 text-right">盈亏%</th>
                    <th className="px-2 py-2 text-right">龙头分</th>
                    <th className="px-2 py-2 text-left">原因</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trade_list.map((t, i) => (
                    <tr key={i} className="border-t border-ink-800">
                      <td className="px-2 py-1.5 font-mono text-amber-300">{t.code}</td>
                      <td className="px-2 py-1.5 text-ink-400">{t.entry_date}</td>
                      <td className="px-2 py-1.5 text-ink-400">{t.exit_date}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{t.entry_price.toFixed(2)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{t.exit_price.toFixed(2)}</td>
                      <td className={`px-2 py-1.5 text-right font-mono ${t.pnl_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                        {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono text-amber-400">{t.dragon_score.toFixed(0)}</td>
                      <td className="px-2 py-1.5 text-ink-400 text-[11px]">{t.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {result?.error && (
        <div className="p-4 rounded bg-red-900/30 ring-1 ring-red-700 text-red-300 text-sm">
          {result.error}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-lg ring-1 ring-ink-800 p-3 bg-ink-900/40">
      <div className="text-[10px] text-ink-500 uppercase tracking-wide">{label}</div>
      <div className={`text-lg font-mono font-bold mt-1 ${color}`}>{value}</div>
    </div>
  );
}


/* ─────────────── Knowledge Panel ─────────────── */

function KnowledgePanel() {
  const [kb, setKb] = useState<{ buy: Record<string, string>; sell: Record<string, string>; cycle: Record<string, string> } | null>(null);

  useEffect(() => {
    api.dragonKnowledge().then(setKb).catch(() => {});
  }, []);

  if (!kb) return <div className="p-10 text-center text-ink-500">加载中...</div>;

  return (
    <div className="p-5 space-y-5 max-w-4xl">
      <Section title="买入模式" icon="fa-arrow-trend-up" color="text-red-400">
        {Object.entries(kb.buy).map(([k, v]) => <KbCard key={k} title={k} desc={v} />)}
      </Section>
      <Section title="卖出模式" icon="fa-arrow-trend-down" color="text-green-400">
        {Object.entries(kb.sell).map(([k, v]) => <KbCard key={k} title={k} desc={v} />)}
      </Section>
      <Section title="市场周期" icon="fa-temperature-half" color="text-amber-400">
        {Object.entries(kb.cycle).map(([k, v]) => {
          const p = PHASE_LABEL[k];
          return <KbCard key={k} title={p ? p.label : k} desc={v} />;
        })}
      </Section>
    </div>
  );
}

function Section({ title, icon, color, children }: { title: string; icon: string; color: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={`text-sm font-semibold mb-2 ${color}`}>
        <i className={`fas ${icon} mr-2`} />
        {title}
      </div>
      <div className="grid grid-cols-2 gap-3">{children}</div>
    </div>
  );
}

function KbCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="rounded-lg ring-1 ring-ink-800 p-3 bg-ink-900/40">
      <div className="text-amber-300 font-semibold text-sm mb-1">{title}</div>
      <div className="text-ink-400 text-xs">{desc}</div>
    </div>
  );
}
