// 交易系统 (M1)
// 左侧：我的交易系统列表（CRUD）
// 右侧：当前系统的 5 段 Pipeline 概览（编辑器留待 M2-M4）

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type QuantBacktestDetail,
  type QuantBacktestResult,
  type QuantBacktestSummary,
  type QuantEquityPoint,
  type QuantOrder,
  type QuantPositionEnd,
  type QuantRunResult,
  type QuantRunSummary,
  type QuantRuleEvalResult,
  type QuantSideReport,
  type QuantSignalRecord,
  type QuantSystem,
  type QuantSystemSummary,
  type QuantTestResult,
  type QuantTrade,
} from "../services/api";
import { toast } from "../components/Toast";
import { ConfirmModal, InputModal } from "../components/Modal";

export function SystemPage() {
  const [list, setList] = useState<QuantSystemSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<QuantSystem | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showNewModal, setShowNewModal] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.quantList();
      setList(r);
      if (r.length > 0 && selectedId === null) setSelectedId(r[0].id);
      if (r.length === 0) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  }, [selectedId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (selectedId === null) return;
    setLoading(true);
    api
      .quantGet(selectedId)
      .then((d) => {
        setDetail(d);
        setErr(null);
      })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [selectedId]);

  const handleCreate = async (templateConfig?: Record<string, unknown>) => {
    try {
      const sys = await api.quantCreate(templateConfig || {});
      await refresh();
      setSelectedId(sys.id);
      setShowNewModal(false);
      toast.success(`已创建交易系统「${sys.name}」`);
    } catch (e: any) {
      toast.error(`新建失败：${e?.message || e}`);
    }
  };

  const doDelete = async () => {
    if (!detail) return;
    setConfirmDelete(false);
    try {
      await api.quantDelete(detail.id);
      setSelectedId(null);
      setDetail(null);
      toast.success("已删除");
      await refresh();
    } catch (e: any) {
      toast.error(`删除失败：${e?.message || e}`);
    }
  };

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* 左：交易系统列表 */}
      <aside className="w-72 border-r border-ink-800 bg-ink-900/50 flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-ink-800">
          <div className="text-sm font-semibold text-white">我的交易系统</div>
          <button
            onClick={() => setShowNewModal(true)}
            className="text-xs px-2 py-1 rounded bg-gold/15 text-gold hover:bg-gold/25"
          >
            ＋ 新建
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {err && <div className="m-3 text-xs text-red-400">加载失败：{err}</div>}
          {list.length === 0 && !err && (
            <div className="px-3 py-6 text-xs text-ink-500 leading-relaxed text-center">
              还没有交易系统。
              <br />
              点 <span className="text-gold">＋ 新建</span> 创建一个，
              <br />
              默认会加载 <span className="text-ink-300">Stage 2 趋势跟随</span> 模板。
            </div>
          )}
          {list.map((s) => (
            <button
              key={s.id}
              onClick={() => setSelectedId(s.id)}
              className={
                "w-full text-left px-3 py-2.5 border-b border-ink-800 transition " +
                (s.id === selectedId
                  ? "bg-ink-800 text-white"
                  : "text-ink-300 hover:bg-ink-850")
              }
            >
              <div className="flex items-center gap-2">
                <span className="flex-1 text-sm font-medium truncate">{s.name}</span>
                <StatusBadge status={s.status} />
              </div>
              <div className="text-[11px] text-ink-500 mt-0.5">
                初始资金 ¥{s.initial_capital.toLocaleString()} · 更新于{" "}
                {new Date(s.updated_at).toLocaleDateString()}
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* 右：详情 */}
      <main className="flex-1 overflow-y-auto">
        {!detail && (
          <div className="h-full flex items-center justify-center text-ink-500 text-sm">
            {loading ? "加载中…" : "← 选择左侧一个交易系统，或点击「＋ 新建」"}
          </div>
        )}
        {detail && (
          <SystemDetail
            system={detail}
            onDelete={() => setConfirmDelete(true)}
            onUpdate={async (patch) => {
              await api.quantUpdate(detail.id, patch);
              const fresh = await api.quantGet(detail.id);
              setDetail(fresh);
              refresh();
              toast.success("已保存");
            }}
          />
        )}
      </main>

      {/* 新建系统 — 策略模板选择器 */}
      {showNewModal && (
        <NewSystemModal
          onClose={() => setShowNewModal(false)}
          onCreate={handleCreate}
        />
      )}

      {/* 确认删除 */}
      {confirmDelete && detail && (
        <ConfirmModal
          title="删除交易系统"
          message={`确定要删除「${detail.name}」吗？关联的所有运行记录、回测和持仓也会一并删除，此操作不可撤销。`}
          confirmLabel="删除"
          danger
          onConfirm={doDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

// ── 新建系统 — 策略模板选择器 ────────────────────────────────

type TemplateInfo = {
  key: string;
  name: string;
  emoji: string;
  desc: string;
  tags: string[];
  config: Record<string, unknown>;
  builtin?: boolean;
  id?: number;
};

function NewSystemModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (config?: Record<string, unknown>) => void;
}) {
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/quant/templates");
        const data = await res.json();
        setTemplates(data);
      } catch {
        // fallback
        setTemplates([]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const builtinTpls = templates.filter((t) => t.builtin !== false);
  const userTpls = templates.filter((t) => t.builtin === false);

  const handlePick = async (tpl: TemplateInfo) => {
    setCreating(tpl.key);
    onCreate(tpl.config);
  };

  const TAG_COLORS: Record<string, string> = {
    趋势: "bg-blue-900/40 text-blue-300",
    低频: "bg-green-900/40 text-green-300",
    适合新手: "bg-emerald-900/40 text-emerald-300",
    突破: "bg-amber-900/40 text-amber-300",
    "高RR": "bg-orange-900/40 text-orange-300",
    中高频: "bg-purple-900/40 text-purple-300",
    短线: "bg-red-900/40 text-red-300",
    高频: "bg-pink-900/40 text-pink-300",
    进阶: "bg-rose-900/40 text-rose-300",
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-2xl mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 pt-5 pb-4 border-b border-ink-800">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">新建交易系统</h2>
              <p className="text-xs text-ink-500 mt-1">选择一个策略模板开始，所有参数都可以在创建后修改</p>
            </div>
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-lg bg-ink-800 hover:bg-ink-700 text-ink-400 hover:text-white flex items-center justify-center"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Template Cards */}
        <div className="p-6 space-y-3 max-h-[60vh] overflow-y-auto">
          {loading ? (
            <div className="text-center text-ink-500 py-8">加载策略模板…</div>
          ) : (
            <>
              {/* 空白系统 */}
              <button
                onClick={() => { setCreating("__blank__"); onCreate(); }}
                disabled={creating !== null}
                className={
                  "w-full text-left p-4 rounded-lg border transition group " +
                  (creating === "__blank__"
                    ? "border-gold bg-gold/10"
                    : "border-ink-700 border-dashed bg-ink-850/50 hover:border-ink-500 hover:bg-ink-800")
                }
              >
                <div className="flex items-center gap-3">
                  <span className="text-2xl">📄</span>
                  <div className="flex-1">
                    <span className="text-sm font-semibold text-white group-hover:text-gold transition">空白系统</span>
                    <p className="text-xs text-ink-500 mt-0.5">从零开始，自定义所有规则</p>
                  </div>
                  <span className="text-xs text-ink-600 group-hover:text-gold transition">
                    {creating === "__blank__" ? "创建中…" : "点击创建 →"}
                  </span>
                </div>
              </button>

              {/* 内置模板 */}
              {builtinTpls.length > 0 && (
                <>
                  <div className="flex items-center gap-3 py-1">
                    <div className="flex-1 border-t border-ink-800" />
                    <span className="text-[10px] text-ink-600">内置模板</span>
                    <div className="flex-1 border-t border-ink-800" />
                  </div>
                  {builtinTpls.map((tpl) => (
                    <TemplateCard key={tpl.key} tpl={tpl} creating={creating} onPick={handlePick} tagColors={TAG_COLORS} />
                  ))}
                </>
              )}

              {/* 用户模板 */}
              {userTpls.length > 0 && (
                <>
                  <div className="flex items-center gap-3 py-1">
                    <div className="flex-1 border-t border-ink-800" />
                    <span className="text-[10px] text-ink-600">我的模板</span>
                    <div className="flex-1 border-t border-ink-800" />
                  </div>
                  {userTpls.map((tpl) => (
                    <TemplateCard
                      key={tpl.key}
                      tpl={tpl}
                      creating={creating}
                      onPick={handlePick}
                      tagColors={TAG_COLORS}
                      onDelete={async () => {
                        if (!tpl.id) return;
                        try {
                          await fetch(`/api/quant/templates/${tpl.id}`, { method: "DELETE" });
                          setTemplates((prev) => prev.filter((t) => t.key !== tpl.key));
                          toast.success(`已删除模板「${tpl.name}」`);
                        } catch {
                          toast.error("删除模板失败");
                        }
                      }}
                    />
                  ))}
                </>
              )}
            </>
          )}
        </div>

        {/* Footer hint */}
        <div className="px-6 py-3 border-t border-ink-800 bg-ink-900/50">
          <div className="text-[10px] text-ink-600 leading-relaxed">
            💡 推荐组合：<span className="text-ink-400">Stage 2 趋势跟随</span>（60% 仓位）+
            <span className="text-ink-400"> VCP 龙头突破</span>（30%）+
            <span className="text-ink-400"> 板块龙头</span>（10%）
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── 模板卡片 ── */
function TemplateCard({
  tpl,
  creating,
  onPick,
  tagColors,
  onDelete,
}: {
  tpl: TemplateInfo;
  creating: string | null;
  onPick: (t: TemplateInfo) => void;
  tagColors: Record<string, string>;
  onDelete?: () => void;
}) {
  const cfg = tpl.config as any;
  return (
    <div
      className={
        "relative w-full text-left p-4 rounded-lg border transition group " +
        (creating === tpl.key
          ? "border-gold bg-gold/10"
          : "border-ink-700 bg-ink-850 hover:border-ink-500 hover:bg-ink-800")
      }
    >
      <button
        onClick={() => onPick(tpl)}
        disabled={creating !== null}
        className="w-full text-left"
      >
        <div className="flex items-start gap-3">
          <span className="text-2xl">{tpl.emoji}</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-white group-hover:text-gold transition">
                {tpl.name}
              </span>
              {!tpl.builtin && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-ink-800 text-ink-500">自定义</span>
              )}
              <div className="flex gap-1">
                {tpl.tags.map((tag) => (
                  <span
                    key={tag}
                    className={
                      "text-[10px] px-1.5 py-0.5 rounded " +
                      (tagColors[tag] || "bg-ink-800 text-ink-400")
                    }
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
            <p className="text-xs text-ink-400 mt-1 leading-relaxed">{tpl.desc}</p>
            <div className="mt-2 text-[10px] text-ink-600">
              选股 {cfg?.universe_cfg?.filters?.length ?? 0} 条规则 ·
              买入 {cfg?.signal_cfg?.buy?.all_of?.length ?? 0} 条 ·
              卖出 {cfg?.signal_cfg?.sell?.any_of?.length ?? 0} 条 ·
              止损 {cfg?.risk_cfg?.stop_loss?.type === "ma"
                ? `跌破 ${cfg?.risk_cfg?.stop_loss?.ma_period} 日线`
                : cfg?.risk_cfg?.stop_loss?.type === "percent"
                ? `${cfg?.risk_cfg?.stop_loss?.percent}%`
                : "ATR"}
            </div>
          </div>
          <div className="shrink-0 mt-1">
            {creating === tpl.key ? (
              <span className="text-xs text-gold animate-pulse">创建中…</span>
            ) : (
              <span className="text-xs text-ink-600 group-hover:text-gold transition">
                点击创建 →
              </span>
            )}
          </div>
        </div>
      </button>
      {onDelete && (
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="absolute top-2 right-2 w-6 h-6 rounded bg-ink-800 hover:bg-red-900/60 text-ink-500 hover:text-red-400 flex items-center justify-center text-xs opacity-0 group-hover:opacity-100 transition-opacity"
          title="删除模板"
        >
          ✕
        </button>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { l: string; cls: string }> = {
    draft: { l: "草稿", cls: "bg-ink-800 text-ink-400" },
    active: { l: "运行中", cls: "bg-green-900/50 text-green-300" },
    paused: { l: "已暂停", cls: "bg-amber-900/50 text-amber-300" },
  };
  const v = map[status] || map.draft;
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${v.cls}`}>{v.l}</span>;
}

function StatusToggle({ status, onToggle }: { status: string; onToggle: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const options = [
    { key: "draft", label: "草稿", desc: "未启用，不参与每日自动运行", cls: "bg-ink-800 text-ink-400" },
    { key: "active", label: "运行中", desc: "启用，每日自动跑 Pipeline", cls: "bg-green-900/50 text-green-300" },
    { key: "paused", label: "已暂停", desc: "暂停自动运行，保留配置", cls: "bg-amber-900/50 text-amber-300" },
  ];
  const current = options.find((o) => o.key === status) || options[0];
  return (
    <div className="relative">
      <button
        className={`text-[10px] px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 ${current.cls}`}
        onClick={() => setOpen(!open)}
        title="点击切换状态"
      >
        {current.label} <i className="fas fa-chevron-down text-[8px] ml-0.5" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 w-[200px] bg-ink-900 border border-ink-700 rounded-lg shadow-xl overflow-hidden">
          {options.map((o) => (
            <button
              key={o.key}
              className={"w-full text-left px-3 py-2 text-[11px] hover:bg-ink-800 transition " + (o.key === status ? "bg-ink-800" : "")}
              onClick={() => { onToggle(o.key); setOpen(false); }}
            >
              <div className="flex items-center gap-2">
                <span className={`px-1.5 py-0.5 rounded ${o.cls}`}>{o.label}</span>
              </div>
              <div className="text-ink-500 text-[10px] mt-0.5">{o.desc}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SystemDetail({
  system,
  onDelete,
  onUpdate,
}: {
  system: QuantSystem;
  onDelete: () => void;
  onUpdate: (patch: Partial<QuantSystem>) => Promise<void>;
}) {
  const [showSaveTemplate, setShowSaveTemplate] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [editingDesc, setEditingDesc] = useState(false);
  const [nameVal, setNameVal] = useState(system.name);
  const [descVal, setDescVal] = useState(system.description || "");
  const nameRef = useRef<HTMLInputElement>(null);
  const descRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setNameVal(system.name);
    setDescVal(system.description || "");
  }, [system.id, system.name, system.description]);

  const saveName = async () => {
    setEditingName(false);
    const v = nameVal.trim();
    if (!v || v === system.name) { setNameVal(system.name); return; }
    await onUpdate({ name: v } as any);
  };

  const saveDesc = async () => {
    setEditingDesc(false);
    const v = descVal.trim();
    if (v === (system.description || "")) return;
    await onUpdate({ description: v || null } as any);
  };

  const handleSaveAsTemplate = async (name: string) => {
    setShowSaveTemplate(false);
    try {
      const res = await fetch("/api/quant/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          emoji: "📋",
          desc: system.description || `从「${system.name}」保存的模板`,
          tags: ["自定义"],
          config: {
            name,
            description: system.description,
            status: "draft",
            initial_capital: system.initial_capital,
            universe_cfg: system.universe_cfg,
            signal_cfg: system.signal_cfg,
            risk_cfg: system.risk_cfg,
            exec_cfg: system.exec_cfg,
          },
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      toast.success(`已保存为模板「${name}」`);
    } catch (e: any) {
      toast.error(`保存模板失败：${e?.message || e}`);
    }
  };

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-5">
      <header className="flex items-start gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            {editingName ? (
              <input
                ref={nameRef}
                autoFocus
                value={nameVal}
                onChange={(e) => setNameVal(e.target.value)}
                onBlur={saveName}
                onKeyDown={(e) => { if (e.key === "Enter") saveName(); if (e.key === "Escape") { setNameVal(system.name); setEditingName(false); } }}
                className="text-xl font-semibold text-white bg-transparent border-b border-gold/50 outline-none w-full py-0.5"
              />
            ) : (
              <h1
                className="text-xl font-semibold text-white cursor-text hover:border-b hover:border-ink-600 transition-colors py-0.5"
                onClick={() => { setEditingName(true); setTimeout(() => nameRef.current?.select(), 0); }}
                title="点击编辑名称"
              >{system.name}</h1>
            )}
            <StatusToggle status={system.status} onToggle={async (newStatus) => {
              await onUpdate({ status: newStatus } as any);
            }} />
          </div>
          {editingDesc ? (
            <textarea
              ref={descRef}
              autoFocus
              value={descVal}
              onChange={(e) => setDescVal(e.target.value)}
              onBlur={saveDesc}
              onKeyDown={(e) => { if (e.key === "Escape") { setDescVal(system.description || ""); setEditingDesc(false); } }}
              rows={2}
              className="text-sm text-ink-300 mt-1.5 leading-relaxed bg-transparent border border-gold/30 rounded px-2 py-1 outline-none w-full resize-none"
              placeholder="添加策略描述…"
            />
          ) : (
            <p
              className="text-sm text-ink-400 mt-1.5 leading-relaxed cursor-text hover:text-ink-300 transition-colors min-h-[1.5em]"
              onClick={() => { setEditingDesc(true); setTimeout(() => descRef.current?.focus(), 0); }}
              title="点击编辑描述"
            >{system.description || <span className="text-ink-600 italic">点击添加描述…</span>}</p>
          )}
          <div className="text-xs text-ink-500 mt-2">
            ID #{system.id} · 初始资金 ¥{system.initial_capital.toLocaleString()} · 创建于{" "}
            {new Date(system.created_at).toLocaleString()}
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowSaveTemplate(true)}
            className="text-xs px-3 py-1.5 rounded bg-gold/15 text-gold hover:bg-gold/25"
          >
            保存为模板
          </button>
          <button
            onClick={onDelete}
            className="text-xs px-3 py-1.5 rounded bg-red-900/40 text-red-300 hover:bg-red-900/60"
          >
            删除
          </button>
        </div>
      </header>

      {showSaveTemplate && (
        <InputModal
          title="保存为模板"
          label="输入模板名称，方便下次创建新系统时直接使用"
          defaultValue={system.name}
          placeholder="如：我的趋势策略"
          confirmLabel="保存"
          onConfirm={handleSaveAsTemplate}
          onCancel={() => setShowSaveTemplate(false)}
        />
      )}

      {/* 5 段 Pipeline 概览 + 内联编辑 */}
      <UniverseSection system={system} onUpdate={onUpdate} />
      <SignalSection system={system} onUpdate={onUpdate} />
      <RiskSection system={system} onUpdate={onUpdate} />
      <ExecSection system={system} onUpdate={onUpdate} />

      {/* M3：触发完整 Pipeline 跑一次 */}
      <DailyRunPanel systemId={system.id} />

      {/* M4：历史回测 */}
      <BacktestPanel systemId={system.id} initialCapital={system.initial_capital} />

      {/* M2：单股信号试运行 */}
      <TestRunPanel systemId={system.id} />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// M3: 完整 Pipeline 触发面板 + 今日委托清单 + 历史 run 列表
// ──────────────────────────────────────────────────────────────
function DailyRunPanel({ systemId }: { systemId: number }) {
  const [endDate, setEndDate] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [latest, setLatest] = useState<QuantRunResult | null>(null);
  const [runs, setRuns] = useState<QuantRunSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"orders" | "signals" | "candidates">("orders");

  const refreshRuns = useCallback(async () => {
    try {
      const r = await api.quantRuns(systemId, 10);
      setRuns(r);
    } catch {
      // 静默
    }
  }, [systemId]);

  useEffect(() => {
    setLatest(null);
    setErr(null);
    refreshRuns();
  }, [systemId, refreshRuns]);

  const run = async () => {
    setRunning(true);
    setErr(null);
    try {
      const r = await api.quantRun(systemId, endDate ? { end_date: endDate } : undefined);
      setLatest(r);
      await refreshRuns();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setRunning(false);
    }
  };

  const loadHistorical = async (runId: number) => {
    try {
      const r = await api.quantRunDetail(runId);
      setLatest(r);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-green-900/40 text-green-300 flex items-center justify-center text-sm font-semibold">
          ▶
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-white">完整 Pipeline 跑一遍</div>
          <div className="text-xs text-ink-500 mt-0.5">
            扫全市场 → 信号求值 → 风控计算手数 → 生成今日委托清单（全程留痕到数据库）
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-500">
          截止日期（可选，留空=今天）
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-ink-850 border border-ink-700 rounded px-2 py-1.5 text-sm text-white w-40 focus:outline-none focus:border-gold/60"
          />
        </label>
        <button
          onClick={run}
          disabled={running}
          className="text-xs px-4 py-1.5 rounded bg-green-900/40 text-green-300 hover:bg-green-900/60 disabled:opacity-50"
        >
          {running ? "运行中…（可能需 30-90 秒）" : "▶ 运行 Pipeline"}
        </button>
        {err && <span className="text-xs text-red-400 ml-2">{err}</span>}
      </div>

      {latest && (
        <div className="mt-4 space-y-3">
          <RunSummaryBar result={latest} />

          {/* Tab 切换 */}
          <div className="flex gap-1 border-b border-ink-800">
            {(
              [
                ["orders", `委托清单 (${latest.orders.length})`],
                ["signals", `信号 (${latest.signals.length})`],
                ["candidates", `候选池 (${latest.candidates.length})`],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setActiveTab(k)}
                className={
                  "px-3 py-1.5 text-xs transition border-b-2 -mb-px " +
                  (activeTab === k
                    ? "border-gold text-white"
                    : "border-transparent text-ink-500 hover:text-ink-300")
                }
              >
                {label}
              </button>
            ))}
          </div>

          {activeTab === "orders" && (
            <OrdersTable
              orders={latest.orders}
              runId={(latest as any).run_id ?? (latest as any).id ?? null}
              systemId={systemId}
            />
          )}
          {activeTab === "signals" && <SignalsList signals={latest.signals} systemId={systemId} />}
          {activeTab === "candidates" && <CandidatesTable result={latest} systemId={systemId} />}
        </div>
      )}

      {runs.length > 0 && (
        <div className="mt-5 pt-4 border-t border-ink-800/60">
          <div className="text-xs text-ink-500 mb-2">最近运行记录</div>
          <div className="space-y-1">
            {runs.map((r) => (
              <button
                key={r.id}
                onClick={() => loadHistorical(r.id)}
                className="w-full flex items-center gap-3 text-xs px-2 py-1.5 rounded hover:bg-ink-850 text-left"
              >
                <span className="text-ink-500 num">#{r.id}</span>
                <span className="font-mono text-ink-300 w-24">{r.trade_date}</span>
                <span className="text-ink-400 w-14">{r.run_type}</span>
                <span className="text-ink-500">
                  候选 <span className="text-ink-200 num">{r.universe_count}</span> · 信号{" "}
                  <span className="text-ink-200 num">{r.signal_count}</span> · 委托{" "}
                  <span className="text-green-300 num">{r.order_count}</span>
                </span>
                <span className="flex-1" />
                <span className="text-ink-600 num">{(r.duration_ms / 1000).toFixed(1)}s</span>
                <span className="text-ink-600">{new Date(r.created_at).toLocaleString()}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function RunSummaryBar({ result }: { result: QuantRunResult }) {
  const m = result.metrics || {};
  const buySignals = m.buy_signals ?? 0;
  const validOrders = result.orders.filter((o: any) => !o.rejected).length;
  const rejectedOrders = result.orders.filter((o: any) => o.rejected).length;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-7 gap-2 text-xs">
        <Stat label="交易日" value={result.trade_date} />
        <Stat label="候选池" value={`${result.universe_count} / ${m.universe_passed ?? "—"}`} />
        <Stat
          label="买入信号"
          value={`${buySignals}`}
          valueColor="text-green-300"
        />
        <Stat
          label="卖出信号"
          value={`${m.sell_signals ?? 0}`}
          valueColor="text-red-300"
        />
        <Stat
          label="生成委托"
          value={`${result.order_count}`}
          valueColor="text-gold"
        />
        <Stat
          label="有效 / 拒单"
          value={`${validOrders} / ${rejectedOrders}`}
          valueColor={rejectedOrders > 0 ? "text-amber-300" : "text-green-300"}
        />
        <Stat
          label="占用资金"
          value={`¥${(m.capital_used ?? 0).toLocaleString()} (${m.capital_used_pct ?? 0}%)`}
        />
      </div>
      {rejectedOrders > 0 && (
        <div className="text-[10px] text-ink-500 bg-ink-850/60 rounded px-3 py-1.5 leading-relaxed">
          💡 <span className="text-ink-400">买入信号 {buySignals}</span> → 风控过滤（仓位上限 / 资金不足 / 单笔亏损限制）→ <span className="text-green-400">有效委托 {validOrders}</span>{rejectedOrders > 0 && <span className="text-red-400"> · 被拒 {rejectedOrders}</span>}
          。被拒原因见委托清单中 ✗ 开头的行。
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  valueColor = "text-white",
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div className="rounded bg-ink-850 px-2 py-1.5">
      <div className="text-[10px] text-ink-500">{label}</div>
      <div className={`text-sm font-mono ${valueColor}`}>{value}</div>
    </div>
  );
}

function OrdersTable({
  orders,
  runId,
  systemId,
}: {
  orders: QuantOrder[];
  runId: number | null;
  systemId: number;
}) {
  const [executed, setExecuted] = useState<Record<number, number>>({}); // order_index → position_id
  const [busy, setBusy] = useState<number | null>(null);
  const [editingOrder, setEditingOrder] = useState<{ idx: number; o: QuantOrder } | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [editQty, setEditQty] = useState("");

  const startMarkExecuted = (idx: number, o: QuantOrder) => {
    if (runId == null) {
      toast.error("此 run 未持久化（可能为历史只读），无法标记。请重新跑一次 Pipeline。");
      return;
    }
    setEditingOrder({ idx, o });
    setEditPrice(String(o.price));
    setEditQty(String(o.qty));
  };

  const confirmMarkExecuted = async () => {
    if (!editingOrder || runId == null) return;
    const { idx } = editingOrder;
    const actualPrice = Number(editPrice) || editingOrder.o.price;
    const actualQty = Number(editQty) || editingOrder.o.qty;
    setEditingOrder(null);
    setBusy(idx);
    try {
      const pos = await api.quantPositionFromOrder({
        run_id: runId,
        order_index: idx,
        actual_price: actualPrice,
        actual_qty: actualQty,
      });
      setExecuted((m) => ({ ...m, [idx]: pos.id }));
      toast.success("已标记成交");
    } catch (e: any) {
      toast.error("标记失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  if (orders.length === 0)
    return (
      <div className="text-xs text-ink-500 py-6 text-center">
        今日无委托。可能原因：所有信号未触发买入、风控全部拒单、或候选池为空。
      </div>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-500 text-left">
          <tr className="border-b border-ink-800">
            <th className="py-2 px-2">代码</th>
            <th className="py-2 px-2">名称</th>
            <th className="py-2 px-2 text-right">买入价</th>
            <th className="py-2 px-2 text-right">止损价</th>
            <th className="py-2 px-2 text-right">手数</th>
            <th className="py-2 px-2 text-right">占用</th>
            <th className="py-2 px-2 text-right">仓位%</th>
            <th className="py-2 px-2 text-right">预估止损亏损</th>
            <th className="py-2 px-2">理由 / 拒单原因</th>
            <th className="py-2 px-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o, i) => {
            const done = executed[i];
            return (
              <tr
                key={i}
                className={
                  "border-b border-ink-800/40 " +
                  (o.rejected ? "text-ink-600" : "text-ink-200 hover:bg-ink-850")
                }
              >
                <td className="py-1.5 px-2 font-mono">
                  <a href={`/stock/${o.code}?strategy=${systemId}`} className="text-gold/80 hover:text-gold hover:underline" onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${o.code}?strategy=${systemId}`; }}>{o.code}</a>
                </td>
                <td className="py-1.5 px-2">{o.name}</td>
                <td className="py-1.5 px-2 text-right font-mono">{o.price.toFixed(2)}</td>
                <td className="py-1.5 px-2 text-right font-mono">{o.stop_price.toFixed(2)}</td>
                <td className="py-1.5 px-2 text-right font-mono">{o.qty.toLocaleString()}</td>
                <td className="py-1.5 px-2 text-right font-mono">
                  ¥{o.notional.toLocaleString()}
                </td>
                <td className="py-1.5 px-2 text-right font-mono">{o.risk_used_pct.toFixed(1)}</td>
                <td className="py-1.5 px-2 text-right font-mono text-red-300">
                  {o.est_loss > 0 ? `-¥${o.est_loss.toLocaleString()}` : "—"}
                </td>
                <td
                  className="py-1.5 px-2 text-ink-500 max-w-[260px] truncate"
                  title={o.rejected ? o.reject_reason : o.reason}
                >
                  {o.rejected ? `✗ ${o.reject_reason}` : o.reason}
                </td>
                <td className="py-1.5 px-2">
                  {o.rejected ? (
                    <span className="text-ink-700 text-[10px]">—</span>
                  ) : done ? (
                    <span
                      className="text-[10px] text-green-300"
                      title={`已生成持仓 #${done}`}
                    >
                      ✓ 已建仓 #{done}
                    </span>
                  ) : (
                    <button
                      onClick={() => startMarkExecuted(i, o)}
                      disabled={busy === i}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-300 hover:bg-green-900/50 disabled:opacity-50"
                    >
                      {busy === i ? "…" : "✓ 标记已成交"}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* 标记成交弹窗 */}
      {editingOrder && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={(e) => e.target === e.currentTarget && setEditingOrder(null)}>
          <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-sm mx-4 p-5">
            <h3 className="text-sm font-semibold text-white mb-3">标记已成交 — {editingOrder.o.code} {editingOrder.o.name}</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-ink-400">实际成交价</label>
                <input type="number" step="0.01" value={editPrice} onChange={(e) => setEditPrice(e.target.value)} className="mt-1 w-full px-3 py-2 text-sm rounded-lg bg-ink-800 border border-ink-600 text-white focus:border-gold focus:outline-none" />
              </div>
              <div>
                <label className="text-xs text-ink-400">实际成交手数</label>
                <input type="number" step="1" value={editQty} onChange={(e) => setEditQty(e.target.value)} className="mt-1 w-full px-3 py-2 text-sm rounded-lg bg-ink-800 border border-ink-600 text-white focus:border-gold focus:outline-none" />
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={() => setEditingOrder(null)} className="px-4 py-2 text-sm rounded-lg border border-ink-700 text-ink-300 hover:bg-ink-800">取消</button>
              <button onClick={confirmMarkExecuted} className="px-4 py-2 text-sm rounded-lg bg-green-700 hover:bg-green-600 text-white font-medium">确认建仓</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SignalsList({ signals, systemId }: { signals: QuantSignalRecord[]; systemId: number }) {
  const [closing, setClosing] = useState<number | null>(null);
  const [closed, setClosed] = useState<Set<number>>(new Set());

  if (signals.length === 0)
    return <div className="text-xs text-ink-500 py-6 text-center">无信号触发。</div>;

  const [closeTarget, setCloseTarget] = useState<QuantSignalRecord | null>(null);
  const [closePrice, setClosePrice] = useState("");

  const quickClose = async (s: QuantSignalRecord) => {
    if (!s.position_id) return;
    setCloseTarget(s);
    setClosePrice(String(s.price));
  };

  const confirmClose = async () => {
    const s = closeTarget;
    if (!s?.position_id) return;
    const exitPrice = Number(closePrice) || s.price;
    setCloseTarget(null);
    setClosing(s.position_id);
    try {
      await api.quantPositionClose(s.position_id, {
        exit_price: exitPrice,
        exit_date: s.date,
        exit_reason: (s.reasons || s.rules_hit.map((r) => r.desc || r.expr)).join(" | "),
        commission: 0,
      });
      setClosed((prev) => new Set(prev).add(s.position_id!));
    } catch (e: any) {
      toast.error("平仓失败: " + (e?.message || e));
    } finally {
      setClosing(null);
    }
  };

  // 排序：持仓卖出信号优先（最重要 — 要立刻处理）
  const sorted = [...signals].sort((a, b) => {
    const aHeld = a.side === "sell" && a.position_id ? 0 : 1;
    const bHeld = b.side === "sell" && b.position_id ? 0 : 1;
    return aHeld - bHeld;
  });

  return (
    <>
    <ul className="space-y-2">
      {sorted.map((s, i) => {
        const isHeldSell = s.side === "sell" && s.position_id != null;
        const wasClosed = isHeldSell && closed.has(s.position_id!);
        return (
          <li
            key={i}
            className={
              "rounded border px-3 py-2 " +
              (isHeldSell
                ? "border-orange-700 bg-orange-900/20"
                : s.side === "buy"
                ? "border-green-800 bg-green-900/15"
                : "border-red-800 bg-red-900/15")
            }
          >
            <div className="flex items-center gap-3 text-xs">
              <span
                className={
                  "px-1.5 py-0.5 rounded font-semibold " +
                  (isHeldSell
                    ? "bg-orange-700 text-white"
                    : s.side === "buy"
                    ? "bg-green-900/60 text-green-200"
                    : "bg-red-900/60 text-red-200")
                }
              >
                {isHeldSell ? "🔔 持仓卖出" : s.side.toUpperCase()}
              </span>
              <a href={`/stock/${s.code}?strategy=${systemId}`} className="font-mono text-gold/80 hover:text-gold hover:underline" onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${s.code}?strategy=${systemId}`; }}>{s.code}</a>
              <span className="text-ink-300">{s.name}</span>
              <span className="text-ink-500 ml-auto font-mono">
                {s.date} @ {s.price.toFixed(2)}
              </span>
            </div>

            {isHeldSell && (
              <div className="mt-2 pl-3 grid grid-cols-2 md:grid-cols-5 gap-2 text-[11px]">
                <div>
                  <div className="text-ink-600">数量</div>
                  <div className="font-mono text-ink-200">{s.qty?.toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-ink-600">买入价</div>
                  <div className="font-mono text-ink-200">{s.entry_price?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-ink-600">止损</div>
                  <div className="font-mono text-ink-200">
                    {s.stop_price?.toFixed(2) || "—"}
                  </div>
                </div>
                <div>
                  <div className="text-ink-600">浮动盈亏</div>
                  <div
                    className={
                      "font-mono " +
                      ((s.pnl_pct ?? 0) >= 0 ? "text-green-300" : "text-red-300")
                    }
                  >
                    {(s.pnl_pct ?? 0) >= 0 ? "+" : ""}
                    {s.pnl_pct?.toFixed(2)}%
                  </div>
                </div>
                <div className="flex items-end">
                  {wasClosed ? (
                    <span className="text-green-300 text-[11px]">✓ 已平仓</span>
                  ) : (
                    <button
                      onClick={() => quickClose(s)}
                      disabled={closing === s.position_id}
                      className="px-2 py-1 rounded bg-orange-700 hover:bg-orange-600 text-white text-[11px] disabled:opacity-50"
                    >
                      {closing === s.position_id ? "处理中…" : "📕 标记已平仓"}
                    </button>
                  )}
                </div>
              </div>
            )}

            {(s.reasons && s.reasons.length > 0
              ? s.reasons
              : s.rules_hit.map((r) => r.desc || r.expr)
            ).length > 0 && (
              <div className="mt-1.5 pl-3 text-[11px] text-ink-500">
                {isHeldSell ? "触发原因：" : `命中 ${s.rules_hit.length} 条规则：`}
                <span className="text-ink-400 ml-1">
                  {(s.reasons && s.reasons.length > 0
                    ? s.reasons
                    : s.rules_hit.map((r) => r.desc || r.expr)
                  ).join(" · ")}
                </span>
              </div>
            )}
          </li>
        );
      })}
    </ul>

    {/* 平仓弹窗 */}
    {closeTarget && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={(e) => e.target === e.currentTarget && setCloseTarget(null)}>
        <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-sm mx-4 p-5">
          <h3 className="text-sm font-semibold text-white mb-3">平仓确认 — {closeTarget.code}</h3>
          <div>
            <label className="text-xs text-ink-400">实际卖出价</label>
            <input type="number" step="0.01" value={closePrice} onChange={(e) => setClosePrice(e.target.value)} className="mt-1 w-full px-3 py-2 text-sm rounded-lg bg-ink-800 border border-ink-600 text-white focus:border-gold focus:outline-none" />
          </div>
          <div className="flex justify-end gap-3 mt-4">
            <button onClick={() => setCloseTarget(null)} className="px-4 py-2 text-sm rounded-lg border border-ink-700 text-ink-300 hover:bg-ink-800">取消</button>
            <button onClick={confirmClose} className="px-4 py-2 text-sm rounded-lg bg-orange-700 hover:bg-orange-600 text-white font-medium">确认平仓</button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}

function CandidatesTable({ result, systemId }: { result: QuantRunResult; systemId: number }) {
  const sigSet = new Set(result.signals.filter((s) => s.side === "buy").map((s) => s.code));
  if (result.candidates.length === 0)
    return <div className="text-xs text-ink-500 py-6 text-center">候选池为空。</div>;
  return (
    <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-500 text-left sticky top-0 bg-ink-900">
          <tr className="border-b border-ink-800">
            <th className="py-2 px-2">代码</th>
            <th className="py-2 px-2">名称</th>
            <th className="py-2 px-2">行业</th>
            <th className="py-2 px-2 text-right">收盘</th>
            <th className="py-2 px-2 text-right">成交额</th>
            <th className="py-2 px-2">买入信号</th>
          </tr>
        </thead>
        <tbody>
          {result.candidates.map((c) => (
            <tr key={c.code} className="border-b border-ink-800/40 text-ink-200 hover:bg-ink-850">
              <td className="py-1 px-2 font-mono">
                <a href={`/stock/${c.code}?strategy=${systemId}`} className="text-gold/80 hover:text-gold hover:underline" onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${c.code}?strategy=${systemId}`; }}>{c.code}</a>
              </td>
              <td className="py-1 px-2">{c.name}</td>
              <td className="py-1 px-2 text-ink-500">{c.industry}</td>
              <td className="py-1 px-2 text-right font-mono">{c.last_close.toFixed(2)}</td>
              <td className="py-1 px-2 text-right font-mono text-ink-400">
                {(c.last_amount / 1e8).toFixed(2)} 亿
              </td>
              <td className="py-1 px-2">
                {sigSet.has(c.code) ? (
                  <span className="text-green-300 text-[10px]">✓ BUY</span>
                ) : (
                  <span className="text-ink-600">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// M4: 历史回测面板（净值曲线 + 指标 + 成交清单 + 历史回测）
// ──────────────────────────────────────────────────────────────
function BacktestPanel({
  systemId,
  initialCapital,
}: {
  systemId: number;
  initialCapital: number;
}) {
  // 默认：过去一年
  const today = new Date();
  const oneYearAgo = new Date(today);
  oneYearAgo.setFullYear(today.getFullYear() - 1);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);

  const [startDate, setStartDate] = useState(fmt(oneYearAgo));
  const [endDate, setEndDate] = useState(fmt(today));
  const [capital, setCapital] = useState(initialCapital);
  const [commissionBps, setCommissionBps] = useState(2.5);
  const [slippageBps, setSlippageBps] = useState(5);
  const [name, setName] = useState("");
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<QuantBacktestResult | QuantBacktestDetail | null>(null);
  const [history, setHistory] = useState<QuantBacktestSummary[]>([]);
  const [tab, setTab] = useState<"curve" | "trades" | "positions" | "stats">("curve");

  const refresh = useCallback(async () => {
    try {
      const r = await api.quantBacktestsList(systemId, 20);
      setHistory(r);
    } catch {
      // ignore
    }
  }, [systemId]);

  useEffect(() => {
    setResult(null);
    setErr(null);
    refresh();
  }, [systemId, refresh]);

  const run = async () => {
    setRunning(true);
    setErr(null);
    try {
      const r = await api.quantBacktest(systemId, {
        start_date: startDate,
        end_date: endDate,
        name: name || undefined,
        commission_bps: commissionBps,
        slippage_bps: slippageBps,
        initial_capital: capital,
      });
      setResult(r);
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setRunning(false);
    }
  };

  const loadHistorical = async (id: number) => {
    try {
      const r = await api.quantBacktestDetail(id);
      setResult(r);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  const [pendingDeleteBt, setPendingDeleteBt] = useState<number | null>(null);

  const removeHistorical = async (id: number) => {
    setPendingDeleteBt(id);
  };

  const confirmDeleteBt = async () => {
    if (pendingDeleteBt == null) return;
    const id = pendingDeleteBt;
    setPendingDeleteBt(null);
    try {
      await api.quantBacktestDelete(id);
      toast.success("已删除回测");
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-purple-900/40 text-purple-300 flex items-center justify-center text-sm font-semibold">
          ⏳
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-white">历史回测 Backtest</div>
          <div className="text-xs text-ink-500 mt-0.5">
            在指定历史区间循环跑 Pipeline，维护持仓/现金账本，输出净值曲线与全部成交记录
          </div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 md:grid-cols-6 gap-3">
        <Field label="开始">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="结束">
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="初始资金">
          <input
            type="number"
            value={capital}
            onChange={(e) => setCapital(Number(e.target.value))}
            className={inputCls}
          />
        </Field>
        <Field label="佣金（万分之）" hint="券商手续费，默认 2.5 = 万分之 2.5 ≈ 万 2.5">
          <input
            type="number"
            step="0.1"
            value={commissionBps}
            onChange={(e) => setCommissionBps(Number(e.target.value))}
            className={inputCls}
          />
        </Field>
        <Field label="滑点（万分之）" hint="模拟实际成交价偏差，默认 5 = 买入价上浮万 5">
          <input
            type="number"
            step="0.1"
            value={slippageBps}
            onChange={(e) => setSlippageBps(Number(e.target.value))}
            className={inputCls}
          />
        </Field>
        <Field label="名称（可选）">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例：2024 Q3"
            className={inputCls}
          />
        </Field>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={run}
          disabled={running}
          className="text-xs px-4 py-1.5 rounded bg-purple-900/40 text-purple-300 hover:bg-purple-900/60 disabled:opacity-50"
        >
          {running ? "回测运行中…（数据量大可能 1-3 分钟）" : "⏳ 跑回测"}
        </button>
        {err && <span className="text-xs text-red-400">{err}</span>}
      </div>

      {result && (
        <div className="mt-4 space-y-3">
          <BacktestMetricsBar result={result} />

          <div className="flex gap-1 border-b border-ink-800">
            {(
              [
                ["curve", "净值曲线"],
                ["trades", `成交 (${result.trades.length})`],
                ["positions", `期末持仓 (${result.positions_end.length})`],
                ["stats", "统计 / 配置"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setTab(k)}
                className={
                  "px-3 py-1.5 text-xs transition border-b-2 -mb-px " +
                  (tab === k
                    ? "border-gold text-white"
                    : "border-transparent text-ink-500 hover:text-ink-300")
                }
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "curve" && <EquityCurveChart curve={result.equity_curve} />}
          {tab === "trades" && <TradesTable trades={result.trades} systemId={systemId} />}
          {tab === "positions" && <PositionsTable positions={result.positions_end} systemId={systemId} />}
          {tab === "stats" && <BacktestStatsPanel result={result} />}
        </div>
      )}

      {history.length > 0 && (
        <div className="mt-5 pt-4 border-t border-ink-800/60">
          <div className="text-xs text-ink-500 mb-2">历史回测</div>
          <div className="space-y-1">
            {history.map((h) => (
              <div
                key={h.id}
                className="flex items-center gap-3 text-xs px-2 py-1.5 rounded hover:bg-ink-850"
              >
                <button
                  onClick={() => loadHistorical(h.id)}
                  className="flex-1 flex items-center gap-3 text-left"
                >
                  <span className="text-ink-500 num">#{h.id}</span>
                  <span className="font-mono text-ink-300 w-44">
                    {h.start_date} → {h.end_date}
                  </span>
                  <span className="text-ink-200 w-32 truncate">{h.name}</span>
                  <span className={
                    "num " +
                    ((h.metrics?.total_return_pct ?? 0) >= 0 ? "text-red-300" : "text-green-300")
                  }>
                    {h.metrics?.total_return_pct >= 0 ? "+" : ""}
                    {h.metrics?.total_return_pct?.toFixed(2) ?? "—"}%
                  </span>
                  <span className="text-ink-500">
                    MDD <span className="text-ink-200 num">{h.metrics?.max_drawdown_pct?.toFixed(2) ?? "—"}%</span>
                  </span>
                  <span className="text-ink-500">
                    成交 <span className="text-ink-200 num">{h.metrics?.trade_count ?? 0}</span>
                  </span>
                  <span className="flex-1" />
                  <span className="text-ink-600 num">
                    {(h.duration_ms / 1000).toFixed(1)}s
                  </span>
                  <span className="text-ink-600">
                    {new Date(h.created_at).toLocaleString()}
                  </span>
                </button>
                <button
                  onClick={() => removeHistorical(h.id)}
                  className="px-2 py-0.5 text-red-400 hover:bg-red-900/40 rounded text-[10px]"
                >
                  删
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 删除回测确认 */}
      {pendingDeleteBt !== null && (
        <ConfirmModal
          title="删除回测"
          message="确定要删除该回测记录？此操作不可恢复。"
          confirmLabel="删除"
          danger
          onConfirm={confirmDeleteBt}
          onCancel={() => setPendingDeleteBt(null)}
        />
      )}
    </section>
  );
}

const inputCls =
  "bg-ink-850 border border-ink-700 rounded px-2 py-1.5 text-sm text-white w-full focus:outline-none focus:border-gold/60";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-500" title={hint}>
      <span>{label}{hint && <span className="text-ink-600 ml-1 cursor-help" title={hint}>ⓘ</span>}</span>
      {children}
    </label>
  );
}

function BacktestMetricsBar({ result }: { result: QuantBacktestResult }) {
  const m = result.metrics;
  const totalRetCls = m.total_return_pct >= 0 ? "text-red-300" : "text-green-300";
  return (
    <div className="grid grid-cols-2 md:grid-cols-7 gap-2 text-xs">
      <Stat label="期末权益" value={`¥${m.final_equity.toLocaleString()}`} />
      <Stat
        label="总收益"
        value={`${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`}
        valueColor={totalRetCls}
      />
      <Stat
        label="年化"
        value={`${m.cagr_pct >= 0 ? "+" : ""}${m.cagr_pct.toFixed(2)}%`}
        valueColor={totalRetCls}
      />
      <Stat label="最大回撤" value={`${m.max_drawdown_pct.toFixed(2)}%`} valueColor="text-yellow-300" />
      <Stat label="夏普" value={m.sharpe.toFixed(2)} />
      <Stat
        label="胜率"
        value={`${m.win_rate_pct.toFixed(1)}% (${m.win_count}/${m.trade_count})`}
      />
      <Stat label="盈亏比 / 仓位" value={`${m.profit_factor.toFixed(2)} · ${m.exposure_pct.toFixed(0)}%`} />
    </div>
  );
}

function EquityCurveChart({ curve }: { curve: QuantEquityPoint[] }) {
  if (curve.length === 0)
    return <div className="text-xs text-ink-500 py-10 text-center">无数据</div>;

  const W = 800;
  const H = 280;
  const padL = 56;
  const padR = 16;
  const padT = 12;
  const padB = 40;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const equities = curve.map((p) => p.equity);
  const peaks: number[] = [];
  let peak = equities[0];
  for (const e of equities) {
    if (e > peak) peak = e;
    peaks.push(peak);
  }
  const drawdowns = curve.map((p) => p.drawdown_pct);

  const minE = Math.min(...equities) * 0.98;
  const maxE = Math.max(...equities, ...peaks) * 1.02;
  const maxDD = Math.max(...drawdowns, 1);

  const x = (i: number) => padL + (i / Math.max(1, curve.length - 1)) * innerW;
  const yE = (v: number) => padT + (1 - (v - minE) / (maxE - minE)) * innerH;
  // 回撤画在下方反向
  const yDD = (v: number) => padT + innerH - (v / maxDD) * (innerH * 0.3);

  const equityPath = curve.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${yE(p.equity)}`).join(" ");
  const peakPath = peaks.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${yE(v)}`).join(" ");
  const ddArea =
    `M ${padL} ${padT + innerH} ` +
    curve.map((p, i) => `L ${x(i)} ${yDD(p.drawdown_pct)}`).join(" ") +
    ` L ${x(curve.length - 1)} ${padT + innerH} Z`;

  // Y 轴 ticks
  const yTicks = 5;
  const ticks = Array.from({ length: yTicks }, (_, i) => {
    const v = minE + (maxE - minE) * (i / (yTicks - 1));
    return { v, y: yE(v) };
  });

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-w-3xl bg-ink-950/40 rounded"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Y 轴网格 */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={W - padR}
              y1={t.y}
              y2={t.y}
              stroke="#27272a"
              strokeDasharray="2 2"
            />
            <text x={padL - 6} y={t.y + 3} textAnchor="end" fontSize="9" fill="#71717a">
              {(t.v / 10000).toFixed(1)}万
            </text>
          </g>
        ))}
        {/* 回撤面积 */}
        <path d={ddArea} fill="#7f1d1d" fillOpacity="0.25" />
        {/* peak 线 */}
        <path d={peakPath} stroke="#52525b" strokeWidth="1" strokeDasharray="3 3" fill="none" />
        {/* equity 线 */}
        <path d={equityPath} stroke="#fbbf24" strokeWidth="1.5" fill="none" />

        {/* X 轴日期 */}
        {[0, Math.floor(curve.length / 4), Math.floor(curve.length / 2), Math.floor((3 * curve.length) / 4), curve.length - 1].map(
          (i) => (
            <text
              key={i}
              x={x(i)}
              y={H - 22}
              textAnchor="middle"
              fontSize="9"
              fill="#71717a"
            >
              {curve[i].date}
            </text>
          ),
        )}
        <text x={padL} y={H - 6} fontSize="9" fill="#a1a1aa">
          黄=权益 · 灰虚=峰值 · 红=回撤
        </text>
      </svg>
    </div>
  );
}

function TradesTable({ trades, systemId }: { trades: QuantTrade[]; systemId: number }) {
  if (trades.length === 0)
    return <div className="text-xs text-ink-500 py-6 text-center">无成交记录</div>;
  return (
    <div className="overflow-x-auto max-h-[480px] overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-500 text-left sticky top-0 bg-ink-900">
          <tr className="border-b border-ink-800">
            <th className="py-2 px-2">日期</th>
            <th className="py-2 px-2">代码</th>
            <th className="py-2 px-2">名称</th>
            <th className="py-2 px-2">方向</th>
            <th className="py-2 px-2 text-right">手数</th>
            <th className="py-2 px-2 text-right">价格</th>
            <th className="py-2 px-2 text-right">盈亏</th>
            <th className="py-2 px-2 text-right">盈亏%</th>
            <th className="py-2 px-2 text-right">持有</th>
            <th className="py-2 px-2">理由</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const isOpen = t.side === "open";
            return (
              <tr key={i} className="border-b border-ink-800/40 hover:bg-ink-850">
                <td className="py-1 px-2 font-mono text-ink-300">{t.date}</td>
                <td className="py-1 px-2 font-mono"><a href={`/stock/${t.code}?strategy=${systemId}`} className="text-gold/80 hover:text-gold hover:underline" onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${t.code}?strategy=${systemId}`; }}>{t.code}</a></td>
                <td className="py-1 px-2 text-ink-200">{t.name}</td>
                <td className="py-1 px-2">
                  <span
                    className={
                      "px-1.5 py-0.5 rounded text-[10px] font-semibold " +
                      (isOpen ? "bg-red-900/60 text-red-200" : "bg-green-900/60 text-green-200")
                    }
                  >
                    {isOpen ? "开仓" : "平仓"}
                  </span>
                </td>
                <td className="py-1 px-2 text-right font-mono">{t.qty.toLocaleString()}</td>
                <td className="py-1 px-2 text-right font-mono">{t.price.toFixed(2)}</td>
                <td
                  className={
                    "py-1 px-2 text-right font-mono " +
                    (t.pnl !== undefined
                      ? t.pnl >= 0
                        ? "text-red-300"
                        : "text-green-300"
                      : "text-ink-600")
                  }
                >
                  {t.pnl !== undefined
                    ? `${t.pnl >= 0 ? "+" : ""}¥${t.pnl.toLocaleString()}`
                    : "—"}
                </td>
                <td
                  className={
                    "py-1 px-2 text-right font-mono " +
                    (t.pnl_pct !== undefined
                      ? t.pnl_pct >= 0
                        ? "text-red-300"
                        : "text-green-300"
                      : "text-ink-600")
                  }
                >
                  {t.pnl_pct !== undefined
                    ? `${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%`
                    : "—"}
                </td>
                <td className="py-1 px-2 text-right font-mono text-ink-400">
                  {t.hold_days !== undefined ? `${t.hold_days}d` : "—"}
                </td>
                <td className="py-1 px-2 text-ink-500 max-w-[260px] truncate" title={t.reason}>
                  {t.reason}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PositionsTable({ positions, systemId }: { positions: QuantPositionEnd[]; systemId: number }) {
  if (positions.length === 0)
    return <div className="text-xs text-ink-500 py-6 text-center">期末空仓</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-500 text-left">
          <tr className="border-b border-ink-800">
            <th className="py-2 px-2">代码</th>
            <th className="py-2 px-2">名称</th>
            <th className="py-2 px-2 text-right">手数</th>
            <th className="py-2 px-2 text-right">成本</th>
            <th className="py-2 px-2 text-right">现价</th>
            <th className="py-2 px-2 text-right">市值</th>
            <th className="py-2 px-2 text-right">浮盈</th>
            <th className="py-2 px-2 text-right">止损</th>
            <th className="py-2 px-2 text-right">入场日</th>
            <th className="py-2 px-2 text-right">持有</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.code} className="border-b border-ink-800/40 hover:bg-ink-850">
              <td className="py-1 px-2 font-mono"><a href={`/stock/${p.code}?strategy=${systemId}`} className="text-gold/80 hover:text-gold hover:underline" onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${p.code}?strategy=${systemId}`; }}>{p.code}</a></td>
              <td className="py-1 px-2 text-ink-200">{p.name}</td>
              <td className="py-1 px-2 text-right font-mono">{p.qty.toLocaleString()}</td>
              <td className="py-1 px-2 text-right font-mono">{p.entry_price.toFixed(2)}</td>
              <td className="py-1 px-2 text-right font-mono">{p.last_price.toFixed(2)}</td>
              <td className="py-1 px-2 text-right font-mono">¥{p.market_value.toLocaleString()}</td>
              <td
                className={
                  "py-1 px-2 text-right font-mono " +
                  (p.pnl_pct >= 0 ? "text-red-300" : "text-green-300")
                }
              >
                {p.pnl_pct >= 0 ? "+" : ""}
                {p.pnl_pct.toFixed(2)}%
              </td>
              <td className="py-1 px-2 text-right font-mono text-yellow-300">
                {p.stop_price.toFixed(2)}
              </td>
              <td className="py-1 px-2 text-right font-mono text-ink-400">{p.entry_date}</td>
              <td className="py-1 px-2 text-right font-mono text-ink-400">{p.hold_days}d</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BacktestStatsPanel({
  result,
}: {
  result: QuantBacktestResult | QuantBacktestDetail;
}) {
  const m = result.metrics;
  const detail = result as QuantBacktestDetail;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
      <div className="rounded border border-ink-800 p-3">
        <div className="text-ink-500 mb-2 text-[11px]">收益统计</div>
        <KVList
          items={[
            ["初始资金", `¥${result.initial_capital.toLocaleString()}`],
            ["期末权益", `¥${m.final_equity.toLocaleString()}`],
            [
              "总收益",
              `${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`,
            ],
            ["年化", `${m.cagr_pct.toFixed(2)}%`],
            ["最大回撤", `${m.max_drawdown_pct.toFixed(2)}%`],
            ["夏普比率", m.sharpe.toFixed(3)],
            ["平均仓位", `${m.exposure_pct.toFixed(1)}%`],
            ["交易日数", String(result.trading_days)],
          ]}
        />
      </div>
      <div className="rounded border border-ink-800 p-3">
        <div className="text-ink-500 mb-2 text-[11px]">交易统计</div>
        <KVList
          items={[
            ["总交易数", String(m.trade_count)],
            ["胜 / 负", `${m.win_count} / ${m.loss_count}`],
            ["胜率", `${m.win_rate_pct.toFixed(2)}%`],
            ["盈亏比", m.profit_factor.toFixed(2)],
            ["平均盈利", `${m.avg_win_pct.toFixed(2)}%`],
            ["平均亏损", `${m.avg_loss_pct.toFixed(2)}%`],
            ["耗时", `${(result.duration_ms / 1000).toFixed(1)}s`],
            ["错误", result.error || "无"],
          ]}
        />
      </div>
      {detail.params && (
        <div className="rounded border border-ink-800 p-3 md:col-span-2">
          <div className="text-ink-500 mb-2 text-[11px]">回测参数</div>
          <KVList
            items={[
              ["佣金", `${detail.params.commission_bps} bps`],
              ["滑点", `${detail.params.slippage_bps} bps`],
              ["成交价模式", detail.params.fill_price_mode || "—"],
            ]}
          />
        </div>
      )}

    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// M2: 单股信号试运行（保留）
// ──────────────────────────────────────────────────────────────
function TestRunPanel({ systemId }: { systemId: number }) {
  const [code, setCode] = useState("600519");
  const [date, setDate] = useState<string>("");
  const [result, setResult] = useState<QuantTestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<{ code: string; name: string }[]>([]);
  const [sugOpen, setSugOpen] = useState(false);
  const [sugIdx, setSugIdx] = useState(0);
  const sugTimer = useRef<ReturnType<typeof setTimeout>>();
  const sugRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setResult(null);
    setErr(null);
  }, [systemId]);

  // Debounced stock search
  useEffect(() => {
    const q = code.trim();
    if (!q) { setSuggestions([]); return; }
    clearTimeout(sugTimer.current);
    sugTimer.current = setTimeout(() => {
      api.searchStocks(q, 8).then((r) => { setSuggestions(r); setSugIdx(0); }).catch(() => {});
    }, 250);
    return () => clearTimeout(sugTimer.current);
  }, [code]);

  // Close suggestions on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (sugRef.current && !sugRef.current.contains(e.target as Node)) setSugOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const pickSuggestion = (c: string) => {
    setCode(c);
    setSugOpen(false);
  };

  const run = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await api.quantTest(systemId, {
        code: code.trim(),
        date: date.trim() || undefined,
      });
      setResult(r);
    } catch (e: any) {
      setErr(String(e?.message || e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-gold/15 text-gold flex items-center justify-center text-sm font-semibold">
          ⚡
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-white">试运行 · 单股信号</div>
          <div className="text-xs text-ink-500 mt-0.5">
            对一只股票（默认取最新交易日）跑一次该系统的买/卖规则，查看每条规则是否命中
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-500">
          股票代码
          <div className="relative" ref={sugRef}>
            <input
              value={code}
              onChange={(e) => { setCode(e.target.value); setSugOpen(true); }}
              onFocus={() => setSugOpen(true)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setSugIdx((i) => Math.min(i + 1, suggestions.length - 1)); }
                else if (e.key === "ArrowUp") { e.preventDefault(); setSugIdx((i) => Math.max(i - 1, 0)); }
                else if (e.key === "Enter" && sugOpen && suggestions[sugIdx]) { e.preventDefault(); pickSuggestion(suggestions[sugIdx].code); }
                else if (e.key === "Enter") run();
                else if (e.key === "Escape") setSugOpen(false);
              }}
              className="bg-ink-850 border border-ink-700 rounded px-2 py-1.5 text-sm text-white font-mono w-44 focus:outline-none focus:border-gold/60"
              placeholder="代码 / 名称"
            />
            {sugOpen && suggestions.length > 0 && (
              <ul className="absolute z-50 top-full left-0 mt-1 w-56 bg-ink-900 border border-ink-700 rounded shadow-lg max-h-52 overflow-y-auto">
                {suggestions.map((s, i) => (
                  <li
                    key={s.code}
                    onMouseDown={() => pickSuggestion(s.code)}
                    className={`flex items-center justify-between px-2 py-1.5 text-xs cursor-pointer ${i === sugIdx ? "bg-ink-700 text-white" : "text-ink-300 hover:bg-ink-800"}`}
                  >
                    <span className="font-mono">{s.code}</span>
                    <span className="text-ink-500 truncate ml-2">{s.name}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-500">
          截止日期（可选）
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="bg-ink-850 border border-ink-700 rounded px-2 py-1.5 text-sm text-white w-40 focus:outline-none focus:border-gold/60"
          />
        </label>
        <button
          onClick={run}
          disabled={loading}
          className="text-xs px-4 py-1.5 rounded bg-gold/20 text-gold hover:bg-gold/30 disabled:opacity-50"
        >
          {loading ? "运行中…" : "运行测试"}
        </button>
        {err && <span className="text-xs text-red-400 ml-2">{err}</span>}
      </div>

      {result && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap gap-4 text-xs text-ink-400 border-b border-ink-800 pb-2">
            <span>
              <span className="text-ink-500">代码</span>{" "}
              <a
                href={`/stock/${result.code}?strategy=${systemId}`}
                onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${result.code}?strategy=${systemId}`; }}
                className="font-mono text-gold/80 hover:text-gold hover:underline"
              >{result.code}</a>
            </span>
            <span>
              <span className="text-ink-500">求值日期</span>{" "}
              <span className="font-mono text-white">{result.date ?? "—"}</span>
            </span>
            <span>
              <span className="text-ink-500">数据条数</span>{" "}
              <span className="font-mono text-white">{result.snapshot.bars}</span>
            </span>
            <span>
              <span className="text-ink-500">收盘</span>{" "}
              <span className="font-mono text-white">{result.snapshot.close.toFixed(2)}</span>
            </span>
            <span>
              <span className="text-ink-500">成交量</span>{" "}
              <span className="font-mono text-white">
                {result.snapshot.vol >= 1e8
                  ? (result.snapshot.vol / 1e8).toFixed(2) + "亿"
                  : result.snapshot.vol >= 1e4
                  ? (result.snapshot.vol / 1e4).toFixed(0) + "万"
                  : result.snapshot.vol.toLocaleString()}
              </span>
            </span>
          </div>

          <SideResult label="买入" report={result.buy} positiveColor="green" />
          <SideResult label="卖出" report={result.sell} positiveColor="red" />

          <FinalVerdict buy={result.buy} sell={result.sell} code={result.code} systemId={systemId} />
        </div>
      )}
    </section>
  );
}

function SideResult({
  label,
  report,
  positiveColor,
}: {
  label: string;
  report: QuantSideReport;
  positiveColor: "green" | "red";
}) {
  if (report.combine === "empty" || report.rules.length === 0) {
    return (
      <div className="text-xs text-ink-500">
        <span className="font-semibold text-ink-300">{label}：</span>无规则
      </div>
    );
  }
  const verdictColor = report.triggered
    ? positiveColor === "green"
      ? "text-green-300 bg-green-900/40"
      : "text-red-300 bg-red-900/40"
    : "text-ink-400 bg-ink-800";
  return (
    <div>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-sm font-semibold text-white">{label}</span>
        <span className="text-[10px] text-ink-500 uppercase tracking-wider">
          ({report.combine === "all_of" ? "全部满足" : "任一满足"})
        </span>
        <span className={`text-[10px] px-2 py-0.5 rounded ${verdictColor}`}>
          {report.triggered ? "✓ 触发" : "未触发"}
        </span>
      </div>
      <ul className="space-y-1">
        {report.rules.map((r, i) => (
          <RuleResultRow key={i} rule={r} />
        ))}
      </ul>
    </div>
  );
}

function RuleResultRow({ rule }: { rule: QuantRuleEvalResult }) {
  const status = rule.error
    ? { icon: "⚠", cls: "text-amber-400" }
    : rule.passed
    ? { icon: "✓", cls: "text-green-400" }
    : { icon: "✗", cls: "text-ink-500" };
  return (
    <li className="flex items-start gap-2 text-xs leading-relaxed">
      <span className={`${status.cls} w-4 text-center font-bold`}>{status.icon}</span>
      <span className="flex-1">
        <code className="text-ink-200 font-mono text-[11px] bg-ink-850 px-1.5 py-0.5 rounded">
          {rule.expr}
        </code>
        {rule.desc && <span className="text-ink-500 ml-2">— {rule.desc}</span>}
        {rule.error && <span className="text-amber-400 ml-2">[错误：{rule.error}]</span>}
        {!rule.error && rule.value !== null && (
          <span className="text-ink-600 ml-2">值={rule.value.toFixed(4)}</span>
        )}
      </span>
    </li>
  );
}

function FinalVerdict({ buy, sell, code, systemId }: { buy: QuantSideReport; sell: QuantSideReport; code: string; systemId: number }) {
  let action: string;
  let cls: string;
  if (buy.triggered && !sell.triggered) {
    action = "BUY · 建议买入";
    cls = "bg-green-900/40 text-green-300 border-green-800";
  } else if (sell.triggered) {
    action = "SELL · 建议卖出";
    cls = "bg-red-900/40 text-red-300 border-red-800";
  } else {
    action = "HOLD · 持仓不动 / 空仓观望";
    cls = "bg-ink-800 text-ink-300 border-ink-700";
  }
  return (
    <div className="mt-3 flex items-center gap-2">
      <div className={`flex-1 px-3 py-2 rounded border ${cls} text-sm font-semibold text-center`}>
        {action}
      </div>
      <a
        href={`/stock/${code}?strategy=${systemId}`}
        onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${code}?strategy=${systemId}`; }}
        className="shrink-0 px-3 py-2 rounded border border-gold/40 bg-gold/10 text-gold text-xs hover:bg-gold/20 flex items-center gap-1.5"
      >
        <i className="fas fa-chart-line text-[10px]" />在K线中查看
      </a>
    </div>
  );
}

function Section({
  i,
  title,
  summary,
  children,
  onEdit,
  editing,
}: {
  i: number;
  title: string;
  summary?: string;
  children?: React.ReactNode;
  onEdit?: () => void;
  editing?: boolean;
}) {
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
      <div
        className={`flex items-center gap-3 ${onEdit ? "cursor-pointer" : ""}`}
        onClick={onEdit}
      >
        <div className="w-8 h-8 rounded-md bg-ink-800 text-gold flex items-center justify-center text-sm font-semibold">
          {i}
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-white">{title}</div>
          {summary && <div className="text-xs text-ink-500 mt-0.5">{summary}</div>}
        </div>
        {onEdit && (
          <span
            className={
              "text-[10px] px-2 py-1 rounded transition " +
              (editing
                ? "bg-gold/20 text-gold"
                : "bg-ink-800 text-ink-400")
            }
          >
            {editing ? "收起" : "✎ 编辑"}
          </span>
        )}
      </div>
      {children && <div className="mt-3 pt-3 border-t border-ink-800/60">{children}</div>}
    </section>
  );
}

// ── Editable Rule List ───────────────────────────────────────

type RuleItem = { expr: string; desc?: string };

// 常用规则模板库
const RULE_TEMPLATES: Record<string, RuleItem[]> = {
  universe: [
    { expr: "ma(amount, 5) > 5e7", desc: "5 日均成交额 > 5000 万" },
    { expr: "ma(amount, 5) > 1e8", desc: "5 日均成交额 > 1 亿" },
    { expr: "close > 5", desc: "股价 > 5 元" },
    { expr: "close > 10", desc: "股价 > 10 元" },
    { expr: "not is_st", desc: "非 ST" },
    { expr: "close > ma(close, 200)", desc: "股价在 200 日线之上" },
    { expr: "close > ma(close, 150)", desc: "股价在 150 日线之上" },
    { expr: "close > ma(close, 60)", desc: "股价在 60 日线之上" },
    { expr: "ma(close, 20) > ma(close, 60)", desc: "20 日线在 60 日线之上" },
  ],
  buy: [
    { expr: "close > ma(close, 150)", desc: "股价 > 150 日线" },
    { expr: "ma(close, 150) > shift(ma(close, 150), 10)", desc: "150 日线向上" },
    { expr: "close > ma(close, 20)", desc: "股价 > 20 日线" },
    { expr: "ma(close, 20) > shift(ma(close, 20), 5)", desc: "20 日线向上" },
    { expr: "close > shift(highest(high, 20), 1)", desc: "突破 20 日最高" },
    { expr: "close > shift(highest(high, 60), 1)", desc: "突破 60 日最高" },
    { expr: "vol > ma(vol, 50) * 1.5", desc: "成交量 > 50 日均量 × 1.5" },
    { expr: "vol > ma(vol, 20) * 2", desc: "成交量 > 20 日均量 × 2" },
    { expr: "close > ma(close, 60) and ma(close, 60) > ma(close, 150)", desc: "多头排列 (60>150)" },
    { expr: "ma(close, 5) > ma(close, 10)", desc: "5 日线 > 10 日线（短期金叉）" },
    { expr: "(close - lowest(low, 20)) / (highest(high, 20) - lowest(low, 20)) > 0.8", desc: "处于 20 日高位区" },
  ],
  sell: [
    { expr: "close < ma(close, 20)", desc: "收盘跌破 20 日线" },
    { expr: "close < ma(close, 10)", desc: "收盘跌破 10 日线" },
    { expr: "close < ma(close, 60)", desc: "收盘跌破 60 日线" },
    { expr: "close < ma(close, 150)", desc: "跌破 150 日线（趋势逆转）" },
    { expr: "(highest(close, 60) - close) / highest(close, 60) > 0.08", desc: "从最高点回撤 > 8%" },
    { expr: "(highest(close, 60) - close) / highest(close, 60) > 0.15", desc: "从最高点回撤 > 15%" },
    { expr: "ma(close, 20) < shift(ma(close, 20), 5)", desc: "20 日线拐头向下" },
    { expr: "vol < ma(vol, 20) * 0.5", desc: "缩量：成交量 < 20 日均量一半" },
  ],
};

function EditableRuleList({
  rules,
  onChange,
  category,
}: {
  rules: RuleItem[];
  onChange: (rules: RuleItem[]) => void;
  category?: "universe" | "buy" | "sell";
}) {
  const [showTemplates, setShowTemplates] = useState(false);
  const update = (idx: number, field: "expr" | "desc", val: string) => {
    const next = rules.map((r, i) =>
      i === idx ? { ...r, [field]: val } : r,
    );
    onChange(next);
  };
  const remove = (idx: number) => onChange(rules.filter((_, i) => i !== idx));
  const add = () => onChange([...rules, { expr: "", desc: "" }]);
  const addTemplate = (t: RuleItem) => {
    // 避免重复添加
    if (rules.some((r) => r.expr === t.expr)) return;
    onChange([...rules, { ...t }]);
  };

  const templates = category ? RULE_TEMPLATES[category] || [] : [];

  return (
    <div className="space-y-2">
      {rules.map((r, idx) => (
        <div key={idx} className="flex items-start gap-2 text-xs">
          <span className="text-ink-600 num w-5 text-right mt-1.5">{idx + 1}.</span>
          <div className="flex-1 flex gap-2">
            <input
              className="flex-1 bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 font-mono text-[11px] focus:border-gold outline-none"
              value={r.expr}
              onChange={(e) => update(idx, "expr", e.target.value)}
              placeholder="DSL 表达式"
            />
            <input
              className="w-48 bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-400 text-[11px] focus:border-gold outline-none"
              value={r.desc || ""}
              onChange={(e) => update(idx, "desc", e.target.value)}
              placeholder="描述（可选）"
            />
          </div>
          <button
            onClick={() => remove(idx)}
            className="text-red-500 hover:text-red-300 mt-1 text-sm"
            title="删除"
          >
            ✕
          </button>
        </div>
      ))}
      <div className="flex gap-2 items-center">
        <button
          onClick={add}
          className="text-[11px] px-2 py-1 rounded bg-ink-800 text-ink-400 hover:text-ink-200"
        >
          ＋ 空白规则
        </button>
        {templates.length > 0 && (
          <button
            onClick={() => setShowTemplates(!showTemplates)}
            className={
              "text-[11px] px-2 py-1 rounded transition " +
              (showTemplates
                ? "bg-gold/15 text-gold"
                : "bg-ink-800 text-ink-400 hover:text-ink-200")
            }
          >
            📋 从模板添加
          </button>
        )}
      </div>
      {showTemplates && templates.length > 0 && (
        <div className="mt-2 p-3 rounded-lg border border-ink-700 bg-ink-900/80 space-y-1">
          <div className="text-[10px] text-ink-500 mb-2">点击添加（已添加的会灰显）：</div>
          {templates.map((t, i) => {
            const exists = rules.some((r) => r.expr === t.expr);
            return (
              <button
                key={i}
                onClick={() => !exists && addTemplate(t)}
                disabled={exists}
                className={
                  "w-full text-left px-2.5 py-1.5 rounded text-[11px] flex items-start gap-2 transition " +
                  (exists
                    ? "bg-ink-850 text-ink-600 cursor-default"
                    : "bg-ink-850 hover:bg-ink-800 text-ink-300 hover:text-white")
                }
              >
                <code className="font-mono text-[10px] flex-1 break-all">
                  {t.expr}
                </code>
                <span className="text-ink-500 text-[10px] shrink-0 max-w-[180px]">
                  {exists ? "✓ 已添加" : t.desc}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── 1. Universe Section ──────────────────────────────────────

function UniverseSection({
  system,
  onUpdate,
}: {
  system: QuantSystem;
  onUpdate: (patch: Partial<QuantSystem>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const cfg = system.universe_cfg;
  const [base, setBase] = useState(cfg?.base ?? "all_a_shares");
  const [maxSize, setMaxSize] = useState(cfg?.max_size ?? 200);
  const [filters, setFilters] = useState<RuleItem[]>(cfg?.filters || []);

  // reset on system change
  useEffect(() => {
    setBase(system.universe_cfg?.base ?? "all_a_shares");
    setMaxSize(system.universe_cfg?.max_size ?? 200);
    setFilters(system.universe_cfg?.filters || []);
  }, [system.id, system.updated_at]);

  const save = async () => {
    setSaving(true);
    try {
      await onUpdate({
        universe_cfg: {
          ...cfg,
          base,
          max_size: maxSize,
          filters: filters.filter((r) => r.expr.trim()),
        },
      });
      setEditing(false);
    } catch (e: any) {
      toast.error("保存失败: " + (e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section
      i={1}
      title="选股池"
      summary={`基准 ${cfg?.base ?? "—"}，${cfg?.filters?.length ?? 0} 条过滤规则，候选池上限 ${cfg?.max_size ?? "—"}`}
      onEdit={() => setEditing(!editing)}
      editing={editing}
    >
      {editing ? (
        <div className="space-y-3">
          <div className="flex gap-4 text-xs">
            <label className="flex items-center gap-2">
              <span className="text-ink-500">基准：</span>
              <select
                className="bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 text-xs outline-none"
                value={base}
                onChange={(e) => setBase(e.target.value)}
              >
                <option value="all_a_shares">全 A 股</option>
                <option value="main_board">主板</option>
                <option value="sme">中小板</option>
                <option value="gem">创业板</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="text-ink-500">上限：</span>
              <input
                type="number"
                className="w-20 bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 font-mono text-xs outline-none"
                value={maxSize}
                onChange={(e) => setMaxSize(Number(e.target.value) || 200)}
              />
            </label>
          </div>
          <div className="text-[11px] text-ink-500 mb-1">过滤规则（DSL 表达式）</div>
          <EditableRuleList rules={filters} onChange={setFilters} category="universe" />
          <div className="flex gap-2 pt-2">
            <button
              onClick={save}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded bg-gold/15 text-gold hover:bg-gold/25 disabled:opacity-50"
            >
              {saving ? "保存中…" : "💾 保存"}
            </button>
            <button
              onClick={() => {
                setFilters(system.universe_cfg?.filters || []);
                setBase(system.universe_cfg?.base ?? "all_a_shares");
                setMaxSize(system.universe_cfg?.max_size ?? 200);
                setEditing(false);
              }}
              className="text-xs px-3 py-1.5 rounded bg-ink-800 text-ink-400 hover:text-ink-200"
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <RuleList rules={cfg?.filters || []} />
      )}
    </Section>
  );
}

// ── 2. Signal Section ────────────────────────────────────────

function SignalSection({
  system,
  onUpdate,
}: {
  system: QuantSystem;
  onUpdate: (patch: Partial<QuantSystem>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const cfg = system.signal_cfg;
  const [buyRules, setBuyRules] = useState<RuleItem[]>(cfg?.buy?.all_of || []);
  const [sellRules, setSellRules] = useState<RuleItem[]>(cfg?.sell?.any_of || []);

  useEffect(() => {
    setBuyRules(system.signal_cfg?.buy?.all_of || []);
    setSellRules(system.signal_cfg?.sell?.any_of || []);
  }, [system.id, system.updated_at]);

  const save = async () => {
    setSaving(true);
    try {
      await onUpdate({
        signal_cfg: {
          buy: { all_of: buyRules.filter((r) => r.expr.trim()) },
          sell: { any_of: sellRules.filter((r) => r.expr.trim()) },
        },
      });
      setEditing(false);
    } catch (e: any) {
      toast.error("保存失败: " + (e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const buyCount = cfg?.buy?.all_of?.length || cfg?.buy?.any_of?.length || 0;
  const sellCount = cfg?.sell?.any_of?.length || cfg?.sell?.all_of?.length || 0;

  return (
    <Section
      i={2}
      title="买卖信号"
      summary={`买入 ${buyCount} 条 · 卖出 ${sellCount} 条`}
      onEdit={() => setEditing(!editing)}
      editing={editing}
    >
      {editing ? (
        <div className="space-y-4">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-green-400 mb-2">
              买入条件 (all_of — 全部满足)
            </div>
            <EditableRuleList rules={buyRules} onChange={setBuyRules} category="buy" />
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-red-400 mb-2">
              卖出条件 (any_of — 任一触发)
            </div>
            <EditableRuleList rules={sellRules} onChange={setSellRules} category="sell" />
          </div>
          <div className="flex gap-2 pt-2">
            <button
              onClick={save}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded bg-gold/15 text-gold hover:bg-gold/25 disabled:opacity-50"
            >
              {saving ? "保存中…" : "💾 保存"}
            </button>
            <button
              onClick={() => {
                setBuyRules(system.signal_cfg?.buy?.all_of || []);
                setSellRules(system.signal_cfg?.sell?.any_of || []);
                setEditing(false);
              }}
              className="text-xs px-3 py-1.5 rounded bg-ink-800 text-ink-400 hover:text-ink-200"
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-green-400 mb-1.5">
              买入条件 (all of)
            </div>
            <RuleList rules={cfg?.buy?.all_of || cfg?.buy?.any_of || []} />
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-red-400 mb-1.5">
              卖出条件 (any of)
            </div>
            <RuleList rules={cfg?.sell?.any_of || cfg?.sell?.all_of || []} />
          </div>
        </div>
      )}
    </Section>
  );
}

// ── 3. Risk Section ──────────────────────────────────────────

function RiskSection({
  system,
  onUpdate,
}: {
  system: QuantSystem;
  onUpdate: (patch: Partial<QuantSystem>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const cfg = system.risk_cfg;
  const [perStock, setPerStock] = useState(cfg?.per_stock_max_pct ?? 10);
  const [totalPos, setTotalPos] = useState(cfg?.total_position_max_pct ?? 80);
  const [perTrade, setPerTrade] = useState(cfg?.per_trade_max_loss_pct ?? 2);
  const [stopType, setStopType] = useState(cfg?.stop_loss?.type ?? "ma");
  const [maPeriod, setMaPeriod] = useState(cfg?.stop_loss?.ma_period ?? 20);
  const [stopPct, setStopPct] = useState(cfg?.stop_loss?.percent ?? 8);
  const [atrMult, setAtrMult] = useState(cfg?.stop_loss?.atr_mult ?? 2);
  const [ddBreaker, setDdBreaker] = useState(cfg?.drawdown_breaker_pct ?? 15);

  useEffect(() => {
    const c = system.risk_cfg;
    setPerStock(c?.per_stock_max_pct ?? 10);
    setTotalPos(c?.total_position_max_pct ?? 80);
    setPerTrade(c?.per_trade_max_loss_pct ?? 2);
    setStopType(c?.stop_loss?.type ?? "ma");
    setMaPeriod(c?.stop_loss?.ma_period ?? 20);
    setStopPct(c?.stop_loss?.percent ?? 8);
    setAtrMult(c?.stop_loss?.atr_mult ?? 2);
    setDdBreaker(c?.drawdown_breaker_pct ?? 15);
  }, [system.id, system.updated_at]);

  const save = async () => {
    setSaving(true);
    try {
      const stop_loss: Record<string, unknown> = { type: stopType };
      if (stopType === "ma") stop_loss.ma_period = maPeriod;
      if (stopType === "percent") stop_loss.percent = stopPct;
      if (stopType === "atr") { stop_loss.atr_period = 14; stop_loss.atr_mult = atrMult; }
      await onUpdate({
        risk_cfg: {
          ...cfg,
          per_stock_max_pct: perStock,
          total_position_max_pct: totalPos,
          per_trade_max_loss_pct: perTrade,
          stop_loss: stop_loss as any,
          drawdown_breaker_pct: ddBreaker,
        },
      });
      setEditing(false);
    } catch (e: any) {
      toast.error("保存失败: " + (e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const numInp = "w-20 bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 font-mono text-xs outline-none text-right";

  const stopDesc =
    cfg?.stop_loss?.type === "ma"
      ? `跌破 ${cfg.stop_loss.ma_period} 日线`
      : cfg?.stop_loss?.type === "percent"
      ? `跌幅 ${cfg.stop_loss.percent}%`
      : `ATR × ${cfg?.stop_loss?.atr_mult}`;

  return (
    <Section
      i={3}
      title="资金 / 风控"
      summary={`单票仓位 ≤ ${cfg?.per_stock_max_pct}% · 总仓位 ≤ ${cfg?.total_position_max_pct}% · 单笔止损 ≤ ${cfg?.per_trade_max_loss_pct}%`}
      onEdit={() => setEditing(!editing)}
      editing={editing}
    >
      {editing ? (
        <div className="space-y-3 text-xs">
          <div className="grid grid-cols-2 gap-x-6 gap-y-3">
            <label className="flex items-center justify-between">
              <span className="text-ink-500">单票最大仓位 %</span>
              <input type="number" className={numInp} value={perStock} onChange={(e) => setPerStock(Number(e.target.value))} />
            </label>
            <label className="flex items-center justify-between">
              <span className="text-ink-500">总仓位上限 %</span>
              <input type="number" className={numInp} value={totalPos} onChange={(e) => setTotalPos(Number(e.target.value))} />
            </label>
            <label className="flex items-center justify-between">
              <span className="text-ink-500">单笔最大亏损 %</span>
              <input type="number" className={numInp} value={perTrade} onChange={(e) => setPerTrade(Number(e.target.value))} step="0.5" />
            </label>
            <label className="flex items-center justify-between">
              <span className="text-ink-500">回撤熔断 %</span>
              <input type="number" className={numInp} value={ddBreaker} onChange={(e) => setDdBreaker(Number(e.target.value))} />
            </label>
          </div>
          <div className="border-t border-ink-800/60 pt-3">
            <div className="text-ink-500 mb-2">止损方式</div>
            <div className="flex gap-3 items-center">
              <select
                className="bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 text-xs outline-none"
                value={stopType}
                onChange={(e) => setStopType(e.target.value as any)}
              >
                <option value="ma">跌破均线</option>
                <option value="percent">跌幅百分比</option>
                <option value="atr">ATR 倍数</option>
              </select>
              {stopType === "ma" && (
                <label className="flex items-center gap-1 text-ink-400">
                  <input type="number" className={numInp} value={maPeriod} onChange={(e) => setMaPeriod(Number(e.target.value))} />
                  <span>日线</span>
                </label>
              )}
              {stopType === "percent" && (
                <label className="flex items-center gap-1 text-ink-400">
                  <input type="number" className={numInp} value={stopPct} step="0.5" onChange={(e) => setStopPct(Number(e.target.value))} />
                  <span>%</span>
                </label>
              )}
              {stopType === "atr" && (
                <label className="flex items-center gap-1 text-ink-400">
                  <span>×</span>
                  <input type="number" className={numInp} value={atrMult} step="0.1" onChange={(e) => setAtrMult(Number(e.target.value))} />
                </label>
              )}
            </div>
          </div>
          <div className="flex gap-2 pt-2">
            <button onClick={save} disabled={saving} className="text-xs px-3 py-1.5 rounded bg-gold/15 text-gold hover:bg-gold/25 disabled:opacity-50">
              {saving ? "保存中…" : "💾 保存"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="text-xs px-3 py-1.5 rounded bg-ink-800 text-ink-400 hover:text-ink-200"
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <KVList
          items={[
            ["单票最大仓位", `${cfg?.per_stock_max_pct}%`],
            ["总仓位上限", `${cfg?.total_position_max_pct}%`],
            ["单笔最大亏损", `${cfg?.per_trade_max_loss_pct}%`],
            ["止损方式", stopDesc],
            ["系统回撤熔断", `${cfg?.drawdown_breaker_pct}%`],
          ]}
        />
      )}
    </Section>
  );
}

// ── 5. Exec Section ──────────────────────────────────────────

function ExecSection({
  system,
  onUpdate,
}: {
  system: QuantSystem;
  onUpdate: (patch: Partial<QuantSystem>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const cfg = system.exec_cfg;
  const [mode, setMode] = useState(cfg?.mode ?? "semi_auto");
  const [maxOrders, setMaxOrders] = useState(cfg?.max_orders_per_day ?? 5);
  const [capital, setCapital] = useState(system.initial_capital);

  useEffect(() => {
    setMode(system.exec_cfg?.mode ?? "semi_auto");
    setMaxOrders(system.exec_cfg?.max_orders_per_day ?? 5);
    setCapital(system.initial_capital);
  }, [system.id, system.updated_at]);

  const save = async () => {
    setSaving(true);
    try {
      await onUpdate({
        initial_capital: capital,
        exec_cfg: {
          ...cfg,
          mode: mode as any,
          max_orders_per_day: maxOrders,
        },
      });
      setEditing(false);
    } catch (e: any) {
      toast.error("保存失败: " + (e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const numInp = "w-28 bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 font-mono text-xs outline-none text-right";

  return (
    <Section
      i={5}
      title="执行 / 回测"
      summary={`初始资金 ¥${system.initial_capital.toLocaleString()} · 模式 ${cfg?.mode} · 每日最多 ${cfg?.max_orders_per_day} 笔`}
      onEdit={() => setEditing(!editing)}
      editing={editing}
    >
      {editing ? (
        <div className="space-y-3 text-xs">
          <div className="grid grid-cols-2 gap-x-6 gap-y-3">
            <label className="flex items-center justify-between">
              <span className="text-ink-500">初始资金 ¥</span>
              <input type="number" className={numInp} value={capital} onChange={(e) => setCapital(Number(e.target.value))} />
            </label>
            <label className="flex items-center justify-between">
              <span className="text-ink-500">每日最多下单笔数</span>
              <input type="number" className={numInp} value={maxOrders} onChange={(e) => setMaxOrders(Number(e.target.value))} />
            </label>
            <label className="flex items-center justify-between">
              <span className="text-ink-500">执行模式</span>
              <select
                className="bg-ink-850 border border-ink-700 rounded px-2 py-1 text-ink-200 text-xs outline-none"
                value={mode}
                onChange={(e) => setMode(e.target.value as "semi_auto" | "manual")}
              >
                <option value="semi_auto">半自动（生成清单）</option>
                <option value="manual">纯手工</option>
              </select>
            </label>
          </div>
          <div className="flex gap-2 pt-2">
            <button onClick={save} disabled={saving} className="text-xs px-3 py-1.5 rounded bg-gold/15 text-gold hover:bg-gold/25 disabled:opacity-50">
              {saving ? "保存中…" : "💾 保存"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="text-xs px-3 py-1.5 rounded bg-ink-800 text-ink-400 hover:text-ink-200"
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <KVList
          items={[
            ["初始资金", `¥${system.initial_capital.toLocaleString()}`],
            ["执行模式", cfg?.mode === "semi_auto" ? "半自动" : "纯手工"],
            ["每日最多下单", `${cfg?.max_orders_per_day} 笔`],
          ]}
        />
      )}
    </Section>
  );
}

function RuleList({ rules }: { rules: { expr: string; desc?: string }[] }) {
  if (rules.length === 0) {
    return <div className="text-xs text-ink-500">（无规则）</div>;
  }
  return (
    <ul className="space-y-1.5">
      {rules.map((r, idx) => (
        <li key={idx} className="flex items-start gap-2 text-xs">
          <span className="text-ink-600 num w-5 text-right">{idx + 1}.</span>
          <span className="flex-1">
            <code className="text-ink-200 font-mono text-[11px] bg-ink-850 px-1.5 py-0.5 rounded">
              {r.expr}
            </code>
            {r.desc && <span className="text-ink-500 ml-2">— {r.desc}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function KVList({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
      {items.map(([k, v]) => (
        <div key={k} className="flex justify-between border-b border-ink-800/40 pb-1">
          <dt className="text-ink-500">{k}</dt>
          <dd className="text-ink-200 font-mono">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
