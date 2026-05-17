// 实盘日志 (M5)
// ──────────────────────────────────────────────────────────────
// 闭环的"事后"侧：持仓 / 成交流水 / 净值曲线 / 汇总指标。
// 数据由 SystemPage 的 DailyRunPanel "标记已成交" 喂入，也可手动新增。
// ──────────────────────────────────────────────────────────────
import { useCallback, useEffect, useState } from "react";
import {
  api,
  type QuantJournalSummary,
  type QuantNavRow,
  type QuantPositionRow,
  type QuantSystemSummary,
} from "../services/api";
import { toast } from "../components/Toast";
import { ConfirmModal } from "../components/Modal";

type TabKey = "positions" | "trades" | "nav";

export function JournalPage() {
  const [systems, setSystems] = useState<QuantSystemSummary[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<TabKey>("positions");
  const [summary, setSummary] = useState<QuantJournalSummary | null>(null);
  const [openPositions, setOpenPositions] = useState<QuantPositionRow[]>([]);
  const [trades, setTrades] = useState<QuantPositionRow[]>([]);
  const [nav, setNav] = useState<QuantNavRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .quantList()
      .then((rs) => {
        setSystems(rs);
        if (rs.length && selected === null) setSelected(rs[0].id);
      })
      .catch((e) => setErr(String(e?.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(async () => {
    if (selected == null) return;
    setErr(null);
    try {
      const [sum, pos, tr, nv] = await Promise.all([
        api.quantJournalSummary(selected),
        api.quantPositions(selected, "open"),
        api.quantTrades(selected, 200),
        api.quantNav(selected, { limit: 500 }),
      ]);
      setSummary(sum);
      setOpenPositions(pos);
      setTrades(tr);
      setNav(nv);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  }, [selected]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const snapshotToday = async () => {
    if (selected == null) return;
    setBusy(true);
    try {
      await api.quantNavSnapshot(selected);
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-6xl mx-auto space-y-4">
        <header className="flex items-end gap-4">
          <div className="flex-1">
            <h1 className="text-xl font-semibold text-white">实盘日志</h1>
            <p className="text-sm text-ink-400 mt-1">
              持仓 · 已平仓成交 · 净值曲线 — 由"今日清单 → 标记已成交"自动归集
            </p>
          </div>
          <label className="text-xs text-ink-500 flex flex-col gap-1">
            系统
            <select
              value={selected ?? ""}
              onChange={(e) => setSelected(Number(e.target.value))}
              className="bg-ink-850 border border-ink-700 rounded px-2 py-1.5 text-sm text-white w-56"
            >
              {systems.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={snapshotToday}
            disabled={busy || selected == null}
            className="text-xs px-3 py-1.5 rounded bg-blue-900/40 text-blue-300 hover:bg-blue-900/60 disabled:opacity-50"
          >
            {busy ? "…" : "📸 抓今日净值快照"}
          </button>
        </header>

        {err && (
          <div className="text-xs text-red-400 px-3 py-2 rounded bg-red-900/20 border border-red-900/40">
            {err}
          </div>
        )}

        {summary && <SummaryBar s={summary} />}

        <div className="flex gap-1 border-b border-ink-800">
          {(
            [
              ["positions", `持仓 (${openPositions.length})`],
              ["trades", `已平仓 (${trades.length})`],
              ["nav", `净值曲线 (${nav.length})`],
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

        {tab === "positions" && (
          <>
            <ManualAddPositionForm systemId={selected} onCreated={refresh} />
            <OpenPositionsTable positions={openPositions} onChanged={refresh} />
          </>
        )}
        {tab === "trades" && <ClosedTradesTable trades={trades} />}
        {tab === "nav" && <NavPanel nav={nav} initialCapital={summary?.initial_capital ?? 0} />}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
function SummaryBar({ s }: { s: QuantJournalSummary }) {
  const realizedCls = s.realized_pnl_total >= 0 ? "text-red-300" : "text-green-300";
  const equity = s.latest_nav?.equity ?? s.initial_capital;
  const totalPct = ((equity - s.initial_capital) / s.initial_capital) * 100;
  const totalCls = totalPct >= 0 ? "text-red-300" : "text-green-300";
  return (
    <div className="grid grid-cols-2 md:grid-cols-7 gap-2 text-xs">
      <Stat label="初始资金" v={`¥${s.initial_capital.toLocaleString()}`} />
      <Stat
        label="当前权益"
        v={`¥${equity.toLocaleString()}`}
        sub={s.latest_nav ? s.latest_nav.trade_date : "未抓快照"}
        valueColor={totalCls}
      />
      <Stat
        label="总收益"
        v={`${totalPct >= 0 ? "+" : ""}${totalPct.toFixed(2)}%`}
        valueColor={totalCls}
      />
      <Stat
        label="已实现"
        v={`${s.realized_pnl_total >= 0 ? "+" : ""}¥${s.realized_pnl_total.toLocaleString()}`}
        sub={`${s.realized_pnl_pct >= 0 ? "+" : ""}${s.realized_pnl_pct.toFixed(2)}%`}
        valueColor={realizedCls}
      />
      <Stat
        label="持仓 / 占用"
        v={`${s.open_count} 票`}
        sub={`¥${s.open_cost.toLocaleString()}`}
      />
      <Stat
        label="胜率"
        v={`${s.win_rate_pct.toFixed(1)}% (${s.win_count}/${s.closed_count})`}
        sub={`盈亏比 ${s.profit_factor.toFixed(2)}`}
      />
      <Stat
        label="平均持有"
        v={`${s.avg_hold_days.toFixed(1)}d`}
        sub={`+${s.avg_win_pct.toFixed(1)}% / ${s.avg_loss_pct.toFixed(1)}%`}
      />
    </div>
  );
}

function Stat({
  label,
  v,
  sub,
  valueColor = "text-white",
}: {
  label: string;
  v: string;
  sub?: string;
  valueColor?: string;
}) {
  return (
    <div className="rounded bg-ink-850 px-3 py-2 border border-ink-800">
      <div className="text-[10px] text-ink-500">{label}</div>
      <div className={`text-sm font-mono ${valueColor}`}>{v}</div>
      {sub && <div className="text-[10px] text-ink-500 mt-0.5">{sub}</div>}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
function ManualAddPositionForm({
  systemId,
  onCreated,
}: {
  systemId: number | null;
  onCreated: () => void;
}) {
  const [expand, setExpand] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [qty, setQty] = useState(100);
  const [price, setPrice] = useState(10);
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10));
  const [stop, setStop] = useState(0);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (systemId == null) return;
    setBusy(true);
    try {
      await api.quantPositionManual({
        system_id: systemId,
        code: code.trim(),
        name: name.trim(),
        qty,
        entry_price: price,
        entry_date: entryDate,
        stop_price: stop,
      });
      setCode("");
      setName("");
      setStop(0);
      setExpand(false);
      onCreated();
    } finally {
      setBusy(false);
    }
  };

  if (!expand)
    return (
      <button
        onClick={() => setExpand(true)}
        className="text-xs px-3 py-1.5 rounded bg-ink-850 text-ink-300 hover:bg-ink-800 border border-ink-700"
      >
        + 手动新增持仓
      </button>
    );

  return (
    <div className="rounded border border-ink-800 bg-ink-900/60 p-3">
      <div className="grid grid-cols-2 md:grid-cols-7 gap-2 items-end text-xs">
        <FieldS l="代码"><input className={inp} value={code} onChange={(e) => setCode(e.target.value)} /></FieldS>
        <FieldS l="名称"><input className={inp} value={name} onChange={(e) => setName(e.target.value)} /></FieldS>
        <FieldS l="手数"><input className={inp} type="number" value={qty} onChange={(e) => setQty(Number(e.target.value))} /></FieldS>
        <FieldS l="价格"><input className={inp} type="number" step="0.01" value={price} onChange={(e) => setPrice(Number(e.target.value))} /></FieldS>
        <FieldS l="入场日"><input className={inp} type="date" value={entryDate} onChange={(e) => setEntryDate(e.target.value)} /></FieldS>
        <FieldS l="止损价"><input className={inp} type="number" step="0.01" value={stop} onChange={(e) => setStop(Number(e.target.value))} /></FieldS>
        <div className="flex gap-1">
          <button onClick={submit} disabled={busy || !code} className="px-3 py-1.5 rounded bg-gold/20 text-gold hover:bg-gold/30 disabled:opacity-50">
            提交
          </button>
          <button onClick={() => setExpand(false)} className="px-2 py-1.5 rounded bg-ink-850 text-ink-400 hover:bg-ink-800">
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

const inp = "bg-ink-850 border border-ink-700 rounded px-2 py-1.5 text-sm text-white w-full focus:outline-none focus:border-gold/60";

function FieldS({ l, children }: { l: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[10px] text-ink-500">
      {l}
      {children}
    </label>
  );
}

// ──────────────────────────────────────────────────────────────
function OpenPositionsTable({
  positions,
  onChanged,
}: {
  positions: QuantPositionRow[];
  onChanged: () => void;
}) {
  if (positions.length === 0)
    return <div className="text-xs text-ink-500 py-8 text-center">暂无持仓</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-500 text-left">
          <tr className="border-b border-ink-800">
            <th className="py-2 px-2">代码</th>
            <th className="py-2 px-2">名称</th>
            <th className="py-2 px-2 text-right">手数</th>
            <th className="py-2 px-2 text-right">成本价</th>
            <th className="py-2 px-2 text-right">成本</th>
            <th className="py-2 px-2 text-right">止损</th>
            <th className="py-2 px-2">入场日</th>
            <th className="py-2 px-2">来源</th>
            <th className="py-2 px-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <PositionRow key={p.id} p={p} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PositionRow({
  p,
  onChanged,
}: {
  p: QuantPositionRow;
  onChanged: () => void;
}) {
  const [closing, setClosing] = useState(false);
  const [editStop, setEditStop] = useState(false);
  const [exitPx, setExitPx] = useState(p.entry_price);
  const [exitDt, setExitDt] = useState(new Date().toISOString().slice(0, 10));
  const [reason, setReason] = useState("");
  const [newStop, setNewStop] = useState(p.stop_price);
  const [busy, setBusy] = useState(false);

  const close = async () => {
    setBusy(true);
    try {
      await api.quantPositionClose(p.id, {
        exit_price: exitPx,
        exit_date: exitDt,
        exit_reason: reason,
      });
      setClosing(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const saveStop = async () => {
    setBusy(true);
    try {
      await api.quantPositionEdit(p.id, { stop_price: newStop });
      setEditStop(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const del = async () => {
    setShowDeleteConfirm(false);
    setBusy(true);
    try {
      await api.quantPositionDelete(p.id);
      toast.success("已删除持仓");
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const projPnl = exitPx * p.qty - p.cost_basis;
  const projPct = p.cost_basis > 0 ? (projPnl / p.cost_basis) * 100 : 0;

  return (
    <>
      <tr className="border-b border-ink-800/40 hover:bg-ink-850">
        <td className="py-1.5 px-2 font-mono">{p.code}</td>
        <td className="py-1.5 px-2 text-ink-200">{p.name}</td>
        <td className="py-1.5 px-2 text-right font-mono">{p.qty.toLocaleString()}</td>
        <td className="py-1.5 px-2 text-right font-mono">{p.entry_price.toFixed(2)}</td>
        <td className="py-1.5 px-2 text-right font-mono">¥{p.cost_basis.toLocaleString()}</td>
        <td className="py-1.5 px-2 text-right font-mono text-yellow-300">
          {editStop ? (
            <input
              type="number"
              step="0.01"
              value={newStop}
              onChange={(e) => setNewStop(Number(e.target.value))}
              className="bg-ink-850 border border-ink-700 rounded px-1 py-0.5 w-20 text-right"
            />
          ) : (
            p.stop_price.toFixed(2)
          )}
        </td>
        <td className="py-1.5 px-2 font-mono text-ink-400">{p.entry_date}</td>
        <td className="py-1.5 px-2 text-ink-500 text-[10px]">
          {p.source_run_id ? `run #${p.source_run_id}` : "手动"}
        </td>
        <td className="py-1.5 px-2">
          <div className="flex gap-1">
            {!editStop ? (
              <button onClick={() => setEditStop(true)} disabled={busy} className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/30 text-yellow-300 hover:bg-yellow-900/50">
                改止损
              </button>
            ) : (
              <>
                <button onClick={saveStop} disabled={busy} className="text-[10px] px-1.5 py-0.5 rounded bg-gold/20 text-gold hover:bg-gold/30">保存</button>
                <button onClick={() => { setEditStop(false); setNewStop(p.stop_price); }} className="text-[10px] px-1.5 py-0.5 rounded bg-ink-800 text-ink-400">×</button>
              </>
            )}
            <button onClick={() => setClosing(!closing)} disabled={busy} className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-300 hover:bg-green-900/50">
              {closing ? "收起" : "平仓"}
            </button>
            <button onClick={() => setShowDeleteConfirm(true)} disabled={busy} className="text-[10px] px-1.5 py-0.5 rounded bg-red-900/30 text-red-300 hover:bg-red-900/50">
              删
            </button>
          </div>
        </td>
      </tr>
      {closing && (
        <tr className="border-b border-ink-800/40 bg-ink-900/40">
          <td colSpan={9} className="px-2 py-2">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs items-end">
              <FieldS l="平仓价"><input className={inp} type="number" step="0.01" value={exitPx} onChange={(e) => setExitPx(Number(e.target.value))} /></FieldS>
              <FieldS l="平仓日"><input className={inp} type="date" value={exitDt} onChange={(e) => setExitDt(e.target.value)} /></FieldS>
              <FieldS l="理由"><input className={inp} value={reason} placeholder="止损 / 卖出信号 / 主观" onChange={(e) => setReason(e.target.value)} /></FieldS>
              <div className="text-ink-400">
                预估盈亏：
                <span className={projPnl >= 0 ? "text-red-300" : "text-green-300"}>
                  {" "}¥{projPnl.toFixed(2)} ({projPct.toFixed(2)}%)
                </span>
              </div>
              <button onClick={close} disabled={busy} className="px-3 py-1.5 rounded bg-green-900/40 text-green-300 hover:bg-green-900/60">
                确认平仓
              </button>
            </div>
          </td>
        </tr>
      )}
      {showDeleteConfirm && (
        <ConfirmModal
          title="删除持仓"
          message={`删除「${p.code} ${p.name}」持仓记录？建议用「平仓」而非删除。`}
          confirmLabel="删除"
          danger
          onConfirm={del}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </>
  );
}

// ──────────────────────────────────────────────────────────────
function ClosedTradesTable({ trades }: { trades: QuantPositionRow[] }) {
  if (trades.length === 0)
    return <div className="text-xs text-ink-500 py-8 text-center">无已平仓记录</div>;
  return (
    <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-500 text-left sticky top-0 bg-ink-900">
          <tr className="border-b border-ink-800">
            <th className="py-2 px-2">代码</th>
            <th className="py-2 px-2">名称</th>
            <th className="py-2 px-2">入场</th>
            <th className="py-2 px-2">平仓</th>
            <th className="py-2 px-2 text-right">手数</th>
            <th className="py-2 px-2 text-right">入场价</th>
            <th className="py-2 px-2 text-right">平仓价</th>
            <th className="py-2 px-2 text-right">盈亏</th>
            <th className="py-2 px-2 text-right">盈亏%</th>
            <th className="py-2 px-2 text-right">持有</th>
            <th className="py-2 px-2">理由</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-ink-800/40 hover:bg-ink-850">
              <td className="py-1 px-2 font-mono">{t.code}</td>
              <td className="py-1 px-2 text-ink-200">{t.name}</td>
              <td className="py-1 px-2 font-mono text-ink-400">{t.entry_date}</td>
              <td className="py-1 px-2 font-mono text-ink-400">{t.exit_date}</td>
              <td className="py-1 px-2 text-right font-mono">{t.qty.toLocaleString()}</td>
              <td className="py-1 px-2 text-right font-mono">{t.entry_price.toFixed(2)}</td>
              <td className="py-1 px-2 text-right font-mono">{t.exit_price.toFixed(2)}</td>
              <td
                className={
                  "py-1 px-2 text-right font-mono " +
                  (t.pnl >= 0 ? "text-red-300" : "text-green-300")
                }
              >
                {t.pnl >= 0 ? "+" : ""}¥{t.pnl.toLocaleString()}
              </td>
              <td
                className={
                  "py-1 px-2 text-right font-mono " +
                  (t.pnl_pct >= 0 ? "text-red-300" : "text-green-300")
                }
              >
                {t.pnl_pct >= 0 ? "+" : ""}
                {t.pnl_pct.toFixed(2)}%
              </td>
              <td className="py-1 px-2 text-right font-mono text-ink-400">{t.hold_days}d</td>
              <td className="py-1 px-2 text-ink-500 max-w-[220px] truncate" title={t.exit_reason}>
                {t.exit_reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
function NavPanel({ nav, initialCapital }: { nav: QuantNavRow[]; initialCapital: number }) {
  if (nav.length === 0)
    return (
      <div className="text-xs text-ink-500 py-8 text-center">
        无净值记录。可点击右上「📸 抓今日净值快照」生成第一条。
      </div>
    );
  return (
    <div className="space-y-3">
      <NavChart nav={nav} initial={initialCapital} />
      <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="text-ink-500 text-left sticky top-0 bg-ink-900">
            <tr className="border-b border-ink-800">
              <th className="py-2 px-2">日期</th>
              <th className="py-2 px-2 text-right">权益</th>
              <th className="py-2 px-2 text-right">现金</th>
              <th className="py-2 px-2 text-right">持仓市值</th>
              <th className="py-2 px-2 text-right">持仓数</th>
              <th className="py-2 px-2 text-right">已实现</th>
              <th className="py-2 px-2 text-right">浮盈</th>
              <th className="py-2 px-2 text-right">回撤</th>
            </tr>
          </thead>
          <tbody>
            {[...nav].reverse().map((n) => (
              <tr key={n.trade_date} className="border-b border-ink-800/40 hover:bg-ink-850">
                <td className="py-1 px-2 font-mono">{n.trade_date}</td>
                <td className="py-1 px-2 text-right font-mono text-white">
                  ¥{n.equity.toLocaleString()}
                </td>
                <td className="py-1 px-2 text-right font-mono text-ink-300">
                  ¥{n.cash.toLocaleString()}
                </td>
                <td className="py-1 px-2 text-right font-mono text-ink-300">
                  ¥{n.positions_value.toLocaleString()}
                </td>
                <td className="py-1 px-2 text-right font-mono text-ink-400">{n.n_positions}</td>
                <td
                  className={
                    "py-1 px-2 text-right font-mono " +
                    (n.realized_pnl_total >= 0 ? "text-red-300" : "text-green-300")
                  }
                >
                  {n.realized_pnl_total >= 0 ? "+" : ""}¥{n.realized_pnl_total.toLocaleString()}
                </td>
                <td
                  className={
                    "py-1 px-2 text-right font-mono " +
                    (n.unrealized_pnl >= 0 ? "text-red-300" : "text-green-300")
                  }
                >
                  {n.unrealized_pnl >= 0 ? "+" : ""}¥{n.unrealized_pnl.toLocaleString()}
                </td>
                <td className="py-1 px-2 text-right font-mono text-yellow-300">
                  {n.drawdown_pct.toFixed(2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function NavChart({ nav, initial }: { nav: QuantNavRow[]; initial: number }) {
  const W = 800;
  const H = 240;
  const padL = 56, padR = 16, padT = 12, padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const eq = nav.map((n) => n.equity);
  const minE = Math.min(...eq, initial) * 0.98;
  const maxE = Math.max(...eq, initial) * 1.02;
  const x = (i: number) => padL + (i / Math.max(1, nav.length - 1)) * innerW;
  const yE = (v: number) => padT + (1 - (v - minE) / (maxE - minE)) * innerH;
  const path = nav.map((n, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${yE(n.equity)}`).join(" ");
  const baseY = yE(initial);

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-3xl bg-ink-950/40 rounded">
        <line x1={padL} x2={W - padR} y1={baseY} y2={baseY} stroke="#52525b" strokeDasharray="3 3" />
        <text x={padL - 6} y={baseY + 3} textAnchor="end" fontSize="9" fill="#71717a">
          初始
        </text>
        <path d={path} stroke="#fbbf24" strokeWidth="1.5" fill="none" />
        {[0, Math.floor(nav.length / 2), nav.length - 1].map((i) => (
          <text key={i} x={x(i)} y={H - 18} textAnchor="middle" fontSize="9" fill="#71717a">
            {nav[i].trade_date}
          </text>
        ))}
        <text x={padL} y={padT + 10} fontSize="10" fill="#a1a1aa">
          权益曲线（实盘）· 灰虚=初始资金
        </text>
      </svg>
    </div>
  );
}
