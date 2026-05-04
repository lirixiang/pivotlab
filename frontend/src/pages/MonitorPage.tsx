import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { ScreenerItem } from "../types";

type UItem = { code: string; name: string; industry: string };

type Row = UItem & {
  price: number;
  change_pct: number;
  signal?: ScreenerItem;
  signalKind?: "breakout" | "bottom" | "watch";
};

const FILTERS = [
  { k: "all", l: "全部" },
  { k: "breakout", l: "突破信号" },
  { k: "bottom", l: "企稳信号" },
  { k: "alert", l: "异动预警" },
];

export function MonitorPage({ onPickStock }: { onPickStock: (code: string) => void }) {
  const [universe, setUniverse] = useState<UItem[]>([]);
  const [signals, setSignals] = useState<{ breakout: ScreenerItem[]; bottom: ScreenerItem[] }>({
    breakout: [],
    bottom: [],
  });
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.universe(),
      api.screener("breakout_pullback", 200),
      api.screener("bottom_stabilize", 200),
    ])
      .then(([u, b, s]) => {
        if (cancelled) return;
        setUniverse(u);
        setSignals({ breakout: b.items, bottom: s.items });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const rows: Row[] = useMemo(() => {
    const bp = new Map(signals.breakout.map((i) => [i.code, i] as const));
    const bs = new Map(signals.bottom.map((i) => [i.code, i] as const));
    return universe.map((u) => {
      const sig = bp.get(u.code) ?? bs.get(u.code);
      const kind = bp.has(u.code) ? "breakout" : bs.has(u.code) ? "bottom" : "watch";
      const price = sig?.price ?? mockPrice(u.code);
      const cp = sig?.change_pct ?? mockChange(u.code);
      return { ...u, price, change_pct: cp, signal: sig, signalKind: kind as Row["signalKind"] };
    });
  }, [universe, signals]);

  const filtered = rows.filter((r) => {
    if (filter === "breakout") return r.signalKind === "breakout";
    if (filter === "bottom") return r.signalKind === "bottom";
    if (filter === "alert") return Math.abs(r.change_pct) >= 3;
    return true;
  });

  const summary = {
    total: rows.length,
    breakout: rows.filter((r) => r.signalKind === "breakout").length,
    bottom: rows.filter((r) => r.signalKind === "bottom").length,
    alerts: rows.filter((r) => Math.abs(r.change_pct) >= 3).length,
  };

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      <div className="px-5 py-4 border-b border-ink-800 grad-head flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-semibold text-white">自选监控</h2>
          <div className="text-[11px] text-ink-500 mt-0.5">
            实时跟踪自选股的形态触发与异动，60 秒自动刷新
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 text-[12px] rounded-md bg-ink-800 ring-soft text-ink-200 hover:text-white">
            <i className="fas fa-bell mr-1" /> 提醒规则
          </button>
          <button className="px-3 py-1.5 text-[12px] rounded-md grad-gold text-ink-950 font-semibold">
            <i className="fas fa-plus mr-1" /> 添加自选
          </button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 p-5 border-b border-ink-800">
        <Card label="自选总数" value={summary.total} hint="全部跟踪标的" color="text-white" />
        <Card label="突破信号" value={summary.breakout} hint="今日突破回踩触发" color="text-gold" />
        <Card label="企稳信号" value={summary.bottom} hint="低位止跌后回升" color="text-sky2" />
        <Card label="异动预警" value={summary.alerts} hint="涨跌幅 ≥ 3%" color="text-cn-up" />
      </div>

      <div className="flex items-center gap-3 px-5 py-2.5 border-b border-ink-800">
        <div className="seg">
          {FILTERS.map((f) => (
            <button
              key={f.k}
              onClick={() => setFilter(f.k)}
              className={filter === f.k ? "on" : ""}
            >
              {f.l}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-ink-500 ml-2">
          显示 {filtered.length} / {rows.length}
        </span>
        <div className="flex-1" />
        <span className="text-[11px] text-ink-500">
          <i className="fas fa-circle text-cn-dn text-[8px] mr-1.5" />
          实时
        </span>
      </div>

      <div className="overflow-y-auto scrollbar flex-1">
        <table className="w-full text-[12px] num">
          <thead className="text-ink-500 text-[10px] tracking-wider uppercase sticky top-0 grad-head z-10">
            <tr className="border-b border-ink-800">
              <th className="text-left font-normal px-5 py-2.5">代码 / 名称</th>
              <th className="text-left font-normal px-2">行业</th>
              <th className="text-right font-normal px-2">现价</th>
              <th className="text-right font-normal px-2">涨跌</th>
              <th className="text-left font-normal px-2">形态</th>
              <th className="text-left font-normal px-2 w-32">信号强度</th>
              <th className="text-right font-normal px-2">触发条件</th>
              <th className="text-right font-normal px-5">操作</th>
            </tr>
          </thead>
          <tbody className="text-ink-200">
            {loading && (
              <tr>
                <td colSpan={8} className="text-center text-ink-500 py-10">
                  <i className="fas fa-circle-notch fa-spin mr-2" /> 加载中...
                </td>
              </tr>
            )}
            {!loading &&
              filtered.map((r) => {
                const up = r.change_pct >= 0;
                const tag =
                  r.signalKind === "breakout"
                    ? { l: "突破回踩", c: "chip-on" }
                    : r.signalKind === "bottom"
                    ? { l: "下跌企稳", c: "chip-dn" }
                    : { l: "观察", c: "" };
                return (
                  <tr
                    key={r.code}
                    onClick={() => onPickStock(r.code)}
                    className="row-hover border-b border-ink-850/60 cursor-pointer"
                  >
                    <td className="px-5 py-2.5">
                      <div className="font-sans text-ink-100">{r.name}</div>
                      <div className="text-[10px] text-ink-500">{r.code}</div>
                    </td>
                    <td className="px-2 text-ink-400">{r.industry}</td>
                    <td className={"text-right " + (up ? "text-cn-up" : "text-cn-dn")}>
                      {r.price.toFixed(2)}
                    </td>
                    <td className={"text-right " + (up ? "text-cn-up" : "text-cn-dn")}>
                      {up ? "+" : ""}
                      {r.change_pct.toFixed(2)}%
                    </td>
                    <td>
                      <span className={"chip " + tag.c}>{tag.l}</span>
                    </td>
                    <td>
                      {r.signal ? (
                        <div className="flex items-center gap-2">
                          <div className="level-bar flex-1">
                            <div
                              className="level-fill"
                              style={{
                                width: Math.min(100, r.signal.score) + "%",
                                background:
                                  r.signalKind === "breakout"
                                    ? "linear-gradient(90deg,#d4a857,#f0c674)"
                                    : "linear-gradient(90deg,#7dd3fc,#bae6fd)",
                              }}
                            />
                          </div>
                          <span
                            className={
                              r.signalKind === "breakout" ? "text-gold" : "text-sky2"
                            }
                          >
                            {Math.round(r.signal.score)}
                          </span>
                        </div>
                      ) : (
                        <span className="text-ink-600">—</span>
                      )}
                    </td>
                    <td className="text-right text-ink-400">
                      {r.signal?.triggers.join(" · ") ?? "—"}
                    </td>
                    <td className="text-right pr-5">
                      <button className="text-gold hover:underline mr-3">查看</button>
                      <button className="text-ink-500 hover:text-white">
                        <i className="far fa-trash-can" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center text-ink-500 py-10">
                  当前过滤条件下没有符合的自选股
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Card({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: number;
  hint: string;
  color: string;
}) {
  return (
    <div className="bg-ink-900 ring-soft rounded-lg p-4">
      <div className="text-[11px] text-ink-500">{label}</div>
      <div className={"num text-2xl mt-1 " + color}>{value}</div>
      <div className="text-[10px] text-ink-600 mt-1">{hint}</div>
    </div>
  );
}

function mockPrice(code: string) {
  return 10 + (parseInt(code) % 1000) / 5;
}
function mockChange(code: string) {
  return ((parseInt(code) % 700) - 300) / 100;
}
