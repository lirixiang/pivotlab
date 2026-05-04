import { useEffect, useState } from "react";
import { WatchlistPanel } from "../components/WatchlistPanel";
import { ChartWorkspace } from "../components/ChartWorkspace";
import { LevelsPanel } from "../components/LevelsPanel";
import { SignalCard } from "../components/SignalCard";
import { ScreenerTable } from "../components/ScreenerTable";
import { api } from "../services/api";
import type { ScreenerItem, StockDetail } from "../types";

export function WorkspacePage({
  code,
  onSelect,
  onScanResults,
  scanCounts,
  breakoutResults,
  bottomResults,
}: {
  code: string;
  onSelect: (c: string) => void;
  onScanResults: (r: { breakout: ScreenerItem[]; bottom: ScreenerItem[] }) => void;
  scanCounts: { breakout: number; bottom: number; high: number };
  breakoutResults: ScreenerItem[];
  bottomResults: ScreenerItem[];
}) {
  const [period, setPeriod] = useState("日线");
  const [data, setData] = useState<StockDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .stock(code)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  const activeSignal =
    breakoutResults.find((it) => it.code === code) ??
    bottomResults.find((it) => it.code === code) ??
    null;

  return (
    <main
      className="grid flex-1"
      style={{ gridTemplateColumns: "280px 1fr 340px", minHeight: "calc(100vh - 84px)" }}
    >
      <WatchlistPanel activeCode={code} onSelect={onSelect} scanCounts={scanCounts} />

      <div className="flex flex-col">
        <ChartWorkspace data={data} loading={loading} period={period} onPeriodChange={setPeriod} />
        <ScreenerTable onSelect={onSelect} onResults={onScanResults} />
      </div>

      <aside className="border-l border-ink-700 bg-ink-900 flex flex-col overflow-y-auto scrollbar">
        <SignalCard
          signal={activeSignal}
          levels={data?.levels ?? []}
          price={data?.quote.price ?? 0}
        />
        <LevelsPanel levels={data?.levels ?? []} price={data?.quote.price ?? 0} />

        <div className="p-4 border-b border-ink-800">
          <div className="flex items-center justify-between mb-3">
            <span className="tag text-ink-500">算法配置</span>
            <span className="chip">默认</span>
          </div>
          <div className="space-y-3 text-[12px]">
            <ConfigSlider label="回看周期" value={120} suffix=" 日" min={30} max={240} />
            <ConfigSlider label="极值灵敏度" value={5} min={2} max={20} />
            <ConfigSlider label="价位聚类容差" value={1.2} suffix="%" min={0.1} max={5} step={0.1} />
            <div className="flex flex-wrap gap-1.5 pt-1">
              <span className="chip chip-on">局部极值</span>
              <span className="chip chip-on">密集成交</span>
              <span className="chip chip-on">均线动态</span>
              <span className="chip">筹码峰</span>
              <span className="chip">斐波回撤</span>
            </div>
          </div>
        </div>

        <div className="p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="tag text-ink-500">压力位历史有效性</span>
            <span className="text-[11px] text-gold">回测</span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="触及反应率" value="86" suffix="%" color="text-white" />
            <Stat label="突破后5日收益" value="+3.4" suffix="%" color="text-cn-up" />
            <Stat label="回踩成功率" value="68" suffix="%" color="text-white" />
          </div>
          <button className="mt-3 w-full py-2 rounded-md bg-ink-850 ring-soft text-[12px] text-ink-200 hover:text-white">
            <i className="fas fa-clock-rotate-left text-[11px] mr-1" /> 查看完整回测报告
          </button>
        </div>
      </aside>
    </main>
  );
}

function ConfigSlider({
  label,
  value,
  min,
  max,
  step = 1,
  suffix = "",
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
}) {
  const [v, setV] = useState(value);
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span className="text-ink-300">{label}</span>
        <span className="num text-ink-200">
          {v}
          {suffix}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={v}
        onChange={(e) => setV(Number(e.target.value))}
        className="w-full accent-gold"
      />
    </div>
  );
}

function Stat({
  label,
  value,
  suffix,
  color,
}: {
  label: string;
  value: string;
  suffix: string;
  color: string;
}) {
  return (
    <div className="bg-ink-850 ring-soft rounded-md p-3 text-center">
      <div className={"num text-lg " + color}>
        {value}
        <span className="text-[10px] text-ink-500">{suffix}</span>
      </div>
      <div className="text-[10px] text-ink-500 mt-0.5">{label}</div>
    </div>
  );
}
