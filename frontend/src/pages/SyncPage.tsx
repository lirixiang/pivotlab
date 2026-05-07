import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { DbStats, SourceConfig, SyncTask } from "../types";

type ScheduleItem = { enabled: boolean; cron: string; label: string; desc: string; next_run?: string };
type ScheduleConfig = Record<string, ScheduleItem>;

// Map SYNC_ACTIONS key → source_registry task_type
const SOURCE_TASK_MAP: Record<string, string> = {
  stocks: "stocks",
  quotes: "quotes",
  financials: "financials",
  concepts: "concepts",
  industry: "industry",
  analyst: "analyst_consensus",
};

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  ok: { label: "可用", color: "text-emerald-400" },
  blocked: { label: "已封锁", color: "text-red-400" },
  deprecated: { label: "已弃用", color: "text-amber-400" },
};

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

function cronHint(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return "";
  const [min, hour, , , dow] = parts;
  const dowMap: Record<string, string> = { "1-5": "工作日", "*": "每天", "1": "周一", "0,6": "周末" };
  const dowStr = dowMap[dow] || `周${dow}`;
  return `${dowStr} ${hour}:${min.padStart(2, "0")}`;
}

export function SyncPage() {
  const [tasks, setTasks] = useState<SyncTask[]>([]);
  const [stats, setStats] = useState<DbStats | null>(null);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [candleDays, setCandleDays] = useState(365);
  const [schedule, setSchedule] = useState<ScheduleConfig>({});
  const [schedDirty, setSchedDirty] = useState(false);
  const [schedSaving, setSchedSaving] = useState(false);
  const [sources, setSources] = useState<SourceConfig>({});
  const [openSourceMenu, setOpenSourceMenu] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(() => {
    api.syncTasks().then(setTasks).catch(() => {});
    api.dbStats().then(setStats).catch(() => {});
  }, []);

  const loadSources = useCallback(() => {
    api.getSources().then(setSources).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    loadSources();
    api.getSchedule().then(setSchedule).catch(() => {});
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh, loadSources]);

  // Close source menu on outside click
  useEffect(() => {
    if (!openSourceMenu) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpenSourceMenu(null);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [openSourceMenu]);

  const switchSource = async (taskType: string, sourceId: string) => {
    setOpenSourceMenu(null);
    try {
      const res = await api.putSources({ [taskType]: sourceId });
      if (res.ok) loadSources();
      else alert(res.error || "切换失败");
    } catch { alert("切换失败"); }
  };

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
            const taskType = SOURCE_TASK_MAP[a.key];
            const srcCfg = taskType ? sources[taskType] : undefined;
            const activeSrc = srcCfg?.sources.find((s) => s.selected);
            const hasMultiple = srcCfg && srcCfg.sources.filter((s) => s.status !== "blocked").length > 1;
            return (
              <div key={a.key} className="bg-ink-900 border border-ink-800 rounded-lg px-5 py-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-ink-800 flex items-center justify-center text-ink-400">
                      <i className={`fas ${a.icon}`} />
                    </div>
                    <div>
                      <div className="text-white text-[13px] font-medium flex items-center gap-2">
                        {a.label}
                        {/* Source badge */}
                        {activeSrc && (
                          <div className="relative" ref={openSourceMenu === a.key ? menuRef : undefined}>
                            <button
                              className={
                                "inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition " +
                                (hasMultiple
                                  ? "bg-ink-800 text-ink-300 hover:text-white hover:bg-ink-700 cursor-pointer"
                                  : "bg-ink-800/50 text-ink-500 cursor-default")
                              }
                              onClick={() => hasMultiple && setOpenSourceMenu(openSourceMenu === a.key ? null : a.key)}
                              title={activeSrc.desc}
                            >
                              <i className="fas fa-database text-[8px]" />
                              {activeSrc.name}
                              {hasMultiple && <i className="fas fa-chevron-down text-[7px] ml-0.5" />}
                            </button>
                            {/* Dropdown */}
                            {openSourceMenu === a.key && srcCfg && (
                              <div className="absolute left-0 top-full mt-1 z-50 w-64 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 animate-in fade-in slide-in-from-top-1 duration-150">
                                <div className="px-3 py-1.5 text-[10px] text-ink-500 font-medium border-b border-ink-700">
                                  选择数据源
                                </div>
                                {srcCfg.sources.map((src) => {
                                  const st = STATUS_LABEL[src.status] || STATUS_LABEL.ok;
                                  const disabled = src.status === "blocked";
                                  return (
                                    <button
                                      key={src.id}
                                      className={
                                        "w-full text-left px-3 py-2 flex items-start gap-2 transition " +
                                        (disabled
                                          ? "opacity-40 cursor-not-allowed"
                                          : src.selected
                                            ? "bg-blue-500/10"
                                            : "hover:bg-ink-800")
                                      }
                                      disabled={disabled}
                                      onClick={() => !disabled && taskType && switchSource(taskType, src.id)}
                                    >
                                      <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-1.5">
                                          {src.selected && <i className="fas fa-check text-blue-400 text-[9px]" />}
                                          <span className={`text-[11px] font-medium ${src.selected ? "text-blue-400" : "text-ink-200"}`}>
                                            {src.name}
                                          </span>
                                          <span className={`text-[9px] ${st.color}`}>{st.label}</span>
                                        </div>
                                        <div className="text-[10px] text-ink-500 mt-0.5">{src.desc}</div>
                                        <div className="text-[9px] text-ink-600 mt-0.5 font-mono truncate">{src.url}</div>
                                      </div>
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
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
          const candleSrcCfg = sources["daily_candles"];
          const candleActiveSrc = candleSrcCfg?.sources.find((s) => s.selected);
          const candleHasMultiple = candleSrcCfg && candleSrcCfg.sources.filter((s) => s.status !== "blocked").length > 1;
          return (
            <div className="bg-ink-900 border border-ink-800 rounded-lg px-5 py-4 mt-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-ink-800 flex items-center justify-center text-ink-400">
                    <i className="fas fa-chart-bar" />
                  </div>
                  <div>
                    <div className="text-white text-[13px] font-medium flex items-center gap-2">
                      历史日线 <span className="text-ink-500 text-[11px] font-normal">(耗时较长)</span>
                      {candleActiveSrc && (
                        <div className="relative" ref={openSourceMenu === "daily_candles" ? menuRef : undefined}>
                          <button
                            className={
                              "inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition " +
                              (candleHasMultiple
                                ? "bg-ink-800 text-ink-300 hover:text-white hover:bg-ink-700 cursor-pointer"
                                : "bg-ink-800/50 text-ink-500 cursor-default")
                            }
                            onClick={() => candleHasMultiple && setOpenSourceMenu(openSourceMenu === "daily_candles" ? null : "daily_candles")}
                            title={candleActiveSrc.desc}
                          >
                            <i className="fas fa-database text-[8px]" />
                            {candleActiveSrc.name}
                            {candleHasMultiple && <i className="fas fa-chevron-down text-[7px] ml-0.5" />}
                          </button>
                          {openSourceMenu === "daily_candles" && candleSrcCfg && (
                            <div className="absolute left-0 top-full mt-1 z-50 w-64 bg-ink-850 border border-ink-700 rounded-lg shadow-xl py-1 animate-in fade-in slide-in-from-top-1 duration-150">
                              <div className="px-3 py-1.5 text-[10px] text-ink-500 font-medium border-b border-ink-700">
                                选择数据源
                              </div>
                              {candleSrcCfg.sources.map((src) => {
                                const st = STATUS_LABEL[src.status] || STATUS_LABEL.ok;
                                const disabled = src.status === "blocked";
                                return (
                                  <button
                                    key={src.id}
                                    className={
                                      "w-full text-left px-3 py-2 flex items-start gap-2 transition " +
                                      (disabled
                                        ? "opacity-40 cursor-not-allowed"
                                        : src.selected
                                          ? "bg-blue-500/10"
                                          : "hover:bg-ink-800")
                                    }
                                    disabled={disabled}
                                    onClick={() => !disabled && switchSource("daily_candles", src.id)}
                                  >
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-1.5">
                                        {src.selected && <i className="fas fa-check text-blue-400 text-[9px]" />}
                                        <span className={`text-[11px] font-medium ${src.selected ? "text-blue-400" : "text-ink-200"}`}>
                                          {src.name}
                                        </span>
                                        <span className={`text-[9px] ${st.color}`}>{st.label}</span>
                                      </div>
                                      <div className="text-[10px] text-ink-500 mt-0.5">{src.desc}</div>
                                    </div>
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}
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

      {/* ── Schedule config ── */}
      <div className="px-6 py-4 border-t border-ink-800">
        <div className="flex items-center justify-between mb-3">
          <div className="text-ink-400 text-[12px] font-medium">
            <i className="fas fa-clock mr-1.5" />
            定时同步
            <span className="text-ink-600 font-normal ml-2">配置自动同步任务（cron 格式）</span>
          </div>
          {schedDirty && (
            <button
              className="px-3 py-1 rounded-md text-[12px] font-medium bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 transition"
              disabled={schedSaving}
              onClick={async () => {
                setSchedSaving(true);
                const payload: Record<string, { enabled: boolean; cron: string }> = {};
                for (const [k, v] of Object.entries(schedule)) {
                  payload[k] = { enabled: v.enabled, cron: v.cron };
                }
                try {
                  const res = await api.putSchedule(payload);
                  if (res.ok) {
                    setSchedDirty(false);
                    api.getSchedule().then(setSchedule).catch(() => {});
                  } else {
                    alert(res.error || "保存失败");
                  }
                } catch { alert("保存失败"); }
                setSchedSaving(false);
              }}
            >
              {schedSaving ? <><i className="fas fa-circle-notch fa-spin mr-1" />保存中</> : <><i className="fas fa-save mr-1" />保存配置</>}
            </button>
          )}
        </div>
        <div className="space-y-2">
          {Object.entries(schedule).map(([key, cfg]) => (
            <div key={key} className="flex items-center gap-3 bg-ink-900 border border-ink-800 rounded-lg px-4 py-3">
              {/* Toggle */}
              <button
                type="button"
                className={`w-10 h-[22px] rounded-full transition-colors duration-200 relative flex-shrink-0 p-0 border-0 outline-none cursor-pointer ${cfg.enabled ? "bg-blue-500" : "bg-ink-700"}`}
                onClick={() => {
                  setSchedule((s) => ({ ...s, [key]: { ...s[key], enabled: !s[key].enabled } }));
                  setSchedDirty(true);
                }}
              >
                <span className={`absolute left-0 top-[3px] w-4 h-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${cfg.enabled ? "translate-x-[22px]" : "translate-x-[3px]"}`} />
              </button>
              {/* Label */}
              <div className="w-28 flex-shrink-0">
                <div className="text-white text-[12px] font-medium">{cfg.label || key}</div>
                <div className="text-ink-600 text-[10px]">{cfg.desc}</div>
              </div>
              {/* Cron input */}
              <input
                className="bg-ink-800 border border-ink-700 rounded px-2 py-1 text-[12px] text-ink-200 w-36 font-mono focus:outline-none focus:border-blue-500/50"
                value={cfg.cron}
                placeholder="分 时 日 月 周"
                onChange={(e) => {
                  setSchedule((s) => ({ ...s, [key]: { ...s[key], cron: e.target.value } }));
                  setSchedDirty(true);
                }}
              />
              {/* Cron human-readable hint */}
              <span className="text-ink-600 text-[10px] flex-shrink-0">
                {cronHint(cfg.cron)}
              </span>
              {/* Next run */}
              {cfg.enabled && cfg.next_run && (
                <span className="text-ink-500 text-[10px] ml-auto flex-shrink-0">
                  <i className="fas fa-clock text-[8px] mr-1" />
                  下次: {cfg.next_run}
                </span>
              )}
            </div>
          ))}
        </div>
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
