import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { DbStats, SyncTask } from "../types";

const SYNC_ACTIONS: {
  key: string;
  label: string;
  desc: string;
  icon: string;
  fn: () => Promise<unknown>;
}[] = [
  { key: "stocks", label: "股票列表", desc: "同步全市场A股代码、名称、市场", icon: "fa-list", fn: () => api.syncStocks() },
  { key: "quotes", label: "实时行情", desc: "Tencent批量抓取全量行情快照", icon: "fa-chart-line", fn: () => api.syncQuotes() },
  { key: "financials", label: "基本面快照", desc: "EPS/ROE/营收增长/净利润 (后台)", icon: "fa-chart-pie", fn: () => api.syncFinancials() },
  { key: "concepts", label: "题材与概念", desc: "股票→概念板块映射 (后台)", icon: "fa-tags", fn: () => api.syncConcepts() },
  { key: "industry", label: "行业数据", desc: "行业归属与板块分类 (后台)", icon: "fa-industry", fn: () => api.syncIndustry() },
  { key: "analyst", label: "机构一致预期", desc: "目标价/评级/EPS预测 (后台)", icon: "fa-bullseye", fn: () => api.syncAnalyst() },
];

function fmtTime(s: string | null) {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function statusBadge(status: string) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    done: { bg: "bg-emerald-500/15", text: "text-emerald-400", label: "完成" },
    running: { bg: "bg-blue-500/15", text: "text-blue-400", label: "同步中" },
    pending: { bg: "bg-amber-500/15", text: "text-amber-400", label: "等待" },
    error: { bg: "bg-red-500/15", text: "text-red-400", label: "失败" },
    started: { bg: "bg-blue-500/15", text: "text-blue-400", label: "已启动" },
  };
  const s = map[status] ?? { bg: "bg-ink-700", text: "text-ink-400", label: status };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${s.bg} ${s.text}`}>
      {status === "running" && <i className="fas fa-circle-notch fa-spin mr-1 text-[9px]" />}
      {s.label}
    </span>
  );
}

function fmtCount(n: number) {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return n.toLocaleString("zh-CN");
}

export function SyncPage() {
  const [tasks, setTasks] = useState<SyncTask[]>([]);
  const [stats, setStats] = useState<DbStats | null>(null);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [candleDays, setCandleDays] = useState(365);

  const refresh = useCallback(() => {
    api.syncTasks().then(setTasks).catch(() => {});
    api.dbStats().then(setStats).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const handleSync = async (key: string, fn: () => Promise<unknown>) => {
    setRunning((s) => new Set(s).add(key));
    try {
      await fn();
    } catch {}
    setTimeout(() => {
      refresh();
      setRunning((s) => { const n = new Set(s); n.delete(key); return n; });
    }, 1500);
  };

  // Aggregate latest task per type
  const latestByType = new Map<string, SyncTask>();
  for (const t of tasks) {
    if (!latestByType.has(t.task_type)) latestByType.set(t.task_type, t);
  }

  const statItems: { label: string; value: string; sub?: string; icon: string }[] = stats
    ? [
        { label: "股票", value: fmtCount(stats.stocks), icon: "fa-list" },
        {
          label: "日线",
          value: fmtCount(stats.daily_candles),
          sub: stats.candle_codes > 0
            ? `${stats.candle_codes} 只 · ${stats.candle_min_date} ~ ${stats.candle_max_date}`
            : "未同步",
          icon: "fa-chart-bar",
        },
        { label: "行情", value: fmtCount(stats.quote_cache), icon: "fa-bolt" },
        { label: "基本面", value: fmtCount(stats.financial_snapshots), icon: "fa-chart-pie" },
        { label: "概念", value: fmtCount(stats.stock_concepts), icon: "fa-tags" },
      ]
    : [];

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-y-auto scrollbar">
      {/* Header */}
      <div className="px-6 py-5 border-b border-ink-800 grad-head">
        <h2 className="text-[15px] font-semibold text-white">数据同步</h2>
        <div className="text-[11px] text-ink-500 mt-0.5">
          管理数据源同步任务，将行情、基本面、题材概念等数据存入本地数据库
        </div>
      </div>

      {/* DB stats cards */}
      {statItems.length > 0 && (
        <div className="grid grid-cols-5 gap-3 px-6 py-4 border-b border-ink-800">
          {statItems.map((s) => (
            <div key={s.label} className="bg-ink-900 border border-ink-800 rounded-lg px-4 py-3">
              <div className="flex items-center gap-2 text-ink-500 text-[11px] mb-1">
                <i className={`fas ${s.icon} text-[10px]`} />
                {s.label}
              </div>
              <div className="num text-white text-lg font-semibold">{s.value}</div>
              {s.sub && <div className="text-ink-500 text-[10px] mt-0.5">{s.sub}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Sync action cards */}
      <div className="px-6 py-4">
        <div className="text-ink-400 text-[12px] font-medium mb-3">
          <i className="fas fa-rotate mr-1.5" />
          同步操作
        </div>
        <div className="space-y-3">
          {SYNC_ACTIONS.map((a) => {
            const latest = latestByType.get(a.key);
            const isRunning = running.has(a.key) || latest?.status === "running";
            const progress = latest && latest.total > 0 ? latest.processed / latest.total : 0;
            return (
              <div key={a.key} className="bg-ink-900 border border-ink-800 rounded-lg px-5 py-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-ink-800 flex items-center justify-center text-ink-400">
                      <i className={`fas ${a.icon}`} />
                    </div>
                    <div>
                      <div className="text-white text-[13px] font-medium">{a.label}</div>
                      <div className="text-ink-500 text-[11px] mt-0.5">{a.desc}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {latest && statusBadge(latest.status)}
                    {latest && latest.total > 0 && (
                      <span className="num text-ink-500 text-[11px]">
                        {latest.processed}/{latest.total}
                      </span>
                    )}
                    <button
                      className={
                        "px-4 py-1.5 rounded-md text-[12px] font-medium transition " +
                        (isRunning
                          ? "bg-ink-800 text-ink-500 cursor-not-allowed"
                          : "bg-ink-800 text-ink-200 hover:text-white hover:bg-ink-700 ring-soft")
                      }
                      disabled={isRunning}
                      onClick={() => handleSync(a.key, a.fn)}
                    >
                      {isRunning ? (
                        <><i className="fas fa-circle-notch fa-spin mr-1.5 text-[10px]" />同步中</>
                      ) : (
                        <><i className="fas fa-play mr-1.5 text-[10px]" />开始同步</>
                      )}
                    </button>
                  </div>
                </div>
                {/* Progress bar */}
                {isRunning && latest && latest.total > 0 && (
                  <div className="mt-3 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded-full transition-all duration-500"
                      style={{ width: `${Math.max(2, progress * 100)}%` }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* ── Historical daily candles sync (special card with days selector) ── */}
        {(() => {
          const candleTask = latestByType.get("daily_candles");
          const isCandleRunning = running.has("daily_candles") || candleTask?.status === "running";
          const candleProgress = candleTask && candleTask.total > 0 ? candleTask.processed / candleTask.total : 0;
          const DAYS_OPTIONS = [
            { label: "近1年", value: 365 },
            { label: "近3年", value: 1095 },
            { label: "近5年", value: 1825 },
          ];
          return (
            <div className="bg-ink-900 border border-ink-800 rounded-lg px-5 py-4 mt-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-ink-800 flex items-center justify-center text-ink-400">
                    <i className="fas fa-chart-bar" />
                  </div>
                  <div>
                    <div className="text-white text-[13px] font-medium">
                      历史日线 <span className="text-ink-500 text-[11px] font-normal">(耗时较长)</span>
                    </div>
                    <div className="text-ink-500 text-[11px] mt-0.5">
                      批量拉取全市场股票历史K线，用于形态筛选和回测分析
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {candleTask && statusBadge(candleTask.status)}
                  {candleTask && candleTask.total > 0 && (
                    <span className="num text-ink-500 text-[11px]">
                      {candleTask.processed}/{candleTask.total}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 mt-3">
                {DAYS_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    className={
                      "px-3 py-1 rounded-md text-[12px] transition " +
                      (candleDays === opt.value
                        ? "bg-blue-500/20 text-blue-400 ring-1 ring-blue-500/40"
                        : "bg-ink-800 text-ink-400 hover:text-white hover:bg-ink-700")
                    }
                    onClick={() => setCandleDays(opt.value)}
                    disabled={isCandleRunning}
                  >
                    {opt.label}
                  </button>
                ))}
                <div className="flex-1" />
                <button
                  className={
                    "px-4 py-1.5 rounded-md text-[12px] font-medium transition " +
                    (isCandleRunning
                      ? "bg-ink-800 text-ink-500 cursor-not-allowed"
                      : "bg-ink-800 text-ink-200 hover:text-white hover:bg-ink-700 ring-soft")
                  }
                  disabled={isCandleRunning}
                  onClick={() => handleSync("daily_candles", () => api.syncCandles(candleDays))}
                >
                  {isCandleRunning ? (
                    <><i className="fas fa-circle-notch fa-spin mr-1.5 text-[10px]" />同步中</>
                  ) : (
                    <><i className="fas fa-play mr-1.5 text-[10px]" />全量同步</>
                  )}
                </button>
              </div>
              {isCandleRunning && candleTask && candleTask.total > 0 && (
                <div className="mt-3 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all duration-500"
                    style={{ width: `${Math.max(2, candleProgress * 100)}%` }}
                  />
                </div>
              )}
            </div>
          );
        })()}
      </div>

      {/* Task history */}
      <div className="px-6 py-4 border-t border-ink-800">
        <div className="text-ink-400 text-[12px] font-medium mb-3">
          <i className="fas fa-clock-rotate-left mr-1.5" />
          同步任务记录
        </div>
        {tasks.length === 0 ? (
          <div className="text-ink-600 text-[12px] py-8 text-center">暂无同步记录</div>
        ) : (
          <div className="space-y-2">
            {tasks.map((t) => (
              <div key={t.id} className="flex items-center justify-between bg-ink-900 border border-ink-800 rounded-lg px-4 py-3 text-[12px]">
                <div className="flex items-center gap-3">
                  <span className="text-white font-medium w-20">{t.task_type}</span>
                  {statusBadge(t.status)}
                  <span className="num text-ink-500">
                    {t.processed}/{t.total}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-ink-500">
                  {t.error_msg && (
                    <span className="text-red-400 truncate max-w-[200px]" title={t.error_msg}>
                      {t.error_msg.slice(0, 40)}
                    </span>
                  )}
                  <span className="num">{fmtTime(t.started_at)}</span>
                  {t.finished_at && (
                    <span className="num text-ink-600">→ {fmtTime(t.finished_at)}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
