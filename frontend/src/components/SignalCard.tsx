import type { Level, ScreenerItem } from "../types";

type Props = {
  signal: ScreenerItem | null;
  levels: Level[];
  price: number;
};

export function SignalCard({ signal, levels, price }: Props) {
  const nearestSupport = levels
    .filter((l) => l.kind === "support" && l.price < price)
    .sort((a, b) => b.price - a.price)[0];
  const nearestResistance = levels
    .filter((l) => l.kind === "resistance" && l.price > price)
    .sort((a, b) => a.price - b.price)[0];

  const entry = signal ? signal.price : price;
  const stop = nearestSupport ? nearestSupport.price : price * 0.97;
  const target = nearestResistance ? nearestResistance.price : price * 1.05;
  const ratio = stop && entry !== stop ? (target - entry) / Math.max(0.01, entry - stop) : 0;

  const score = signal?.score ?? 0;
  const triggered = !!signal;

  const breakdown = [
    { name: "突破力度（量价配合）", val: triggered ? Math.min(30, score * 0.3) : 0, max: 30 },
    { name: "回踩缩量验证", val: triggered ? Math.min(25, score * 0.26) : 0, max: 25 },
    { name: "压力位历史强度", val: triggered ? Math.min(25, score * 0.25) : 0, max: 25 },
    { name: "多周期共振", val: triggered ? Math.min(20, score * 0.2) : 0, max: 20 },
  ];

  return (
    <div className="p-4 border-b border-ink-800">
      <div className="flex items-center justify-between mb-2">
        <span className="tag text-ink-500">实时形态</span>
        {triggered ? (
          <span className="text-[11px] text-cn-up flex items-center gap-1.5">
            <span className="dot bg-cn-up" />
            触发中
          </span>
        ) : (
          <span className="text-[11px] text-ink-500 flex items-center gap-1.5">
            <span className="dot bg-ink-500" />
            未触发
          </span>
        )}
      </div>

      <div className="rounded-lg ring-soft grad-card p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[15px] text-white font-semibold tracking-wide">
              {signal
                ? signal.pattern === "breakout_pullback"
                  ? "突破回踩确认"
                  : "下跌企稳形态"
                : "观察中"}
            </div>
            <div className="text-[11px] text-ink-500 mt-0.5">
              日线
              {nearestResistance && ` · 压力 ${nearestResistance.price.toFixed(2)}`}
            </div>
          </div>
          <div className="text-right">
            <div className="num text-2xl text-gold leading-none">
              {Math.round(score)}
              <span className="text-[11px] text-ink-500 ml-1">/100</span>
            </div>
            <div className="text-[10px] text-gold mt-1">
              {score >= 80 ? "高强信号" : score >= 60 ? "中等信号" : score > 0 ? "弱信号" : "—"}
            </div>
          </div>
        </div>

        <div className="mt-4 space-y-2.5">
          {breakdown.map((b) => (
            <div key={b.name}>
              <div className="flex justify-between text-[11px] text-ink-300">
                <span>{b.name}</span>
                <span className="num text-ink-200">
                  {b.val.toFixed(0)} / {b.max}
                </span>
              </div>
              <div className="level-bar mt-1">
                <div
                  className="level-fill"
                  style={{ width: (b.val / b.max) * 100 + "%", background: "#d4a857" }}
                />
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 pt-3 border-t border-ink-800 grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-[10px] text-ink-500 tag">参考入场</div>
            <div className="num text-cn-up text-sm mt-1">{entry.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-[10px] text-ink-500 tag">止损</div>
            <div className="num text-cn-dn text-sm mt-1">{stop.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-[10px] text-ink-500 tag">目标</div>
            <div className="num text-gold text-sm mt-1">{target.toFixed(2)}</div>
          </div>
        </div>
        <div className="mt-2 text-[10px] text-ink-500 text-center">
          盈亏比 <span className="text-ink-200 num">1 : {ratio.toFixed(2)}</span> · 仅供参考
        </div>
      </div>
    </div>
  );
}
