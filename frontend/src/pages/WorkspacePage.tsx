import { useEffect, useState, useCallback } from "react";
import { WatchlistPanel } from "../components/WatchlistPanel";
import { ChartWorkspace } from "../components/ChartWorkspace";
import { LevelsPanel } from "../components/LevelsPanel";
import { ScreenerTable } from "../components/ScreenerTable";
import { api } from "../services/api";
import type { ScreenerItem, StockDetail, SrFactor } from "../types";

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
  const [refreshing, setRefreshing] = useState(false);
  const [algorithm, setAlgorithm] = useState<"classic" | "multifactor">("multifactor");
  const [factors, setFactors] = useState<SrFactor[]>([]);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [minScore, setMinScore] = useState(80);
  const [watchedCodes, setWatchedCodes] = useState<Set<string>>(new Set());
  const [watchRefreshKey, setWatchRefreshKey] = useState(0);

  // Load watchlist codes
  const loadWatchedCodes = useCallback(() => {
    api.watchlist().then((items) => {
      setWatchedCodes(new Set(items.map((i) => i.code)));
    }).catch(() => {});
  }, []);

  useEffect(() => { loadWatchedCodes(); }, [loadWatchedCodes]);

  const toggleWatch = useCallback(() => {
    if (watchedCodes.has(code)) {
      api.removeWatch(code).then(() => {
        setWatchedCodes((prev) => { const s = new Set(prev); s.delete(code); return s; });
        setWatchRefreshKey((k) => k + 1);
      }).catch(() => {});
    } else {
      const name = data?.quote?.name || "";
      api.addWatch(code, name).then(() => {
        setWatchedCodes((prev) => new Set(prev).add(code));
        setWatchRefreshKey((k) => k + 1);
      }).catch(() => {});
    }
  }, [code, watchedCodes, data]);

  const PERIOD_MAP: Record<string, string> = {
    "日线": "daily",
    "周线": "weekly",
    "月线": "monthly",
    "季线": "quarterly",
  };

  // Load available factors on mount + restore saved settings
  useEffect(() => {
    api.srFactors().then((f) => {
      setFactors(f);
      const defaultW: Record<string, number> = {};
      for (const fac of f) defaultW[fac.key] = fac.default_weight;
      // Try to restore from DB
      api.getSetting("sr_config").then((res) => {
        const saved = res.value as Record<string, unknown>;
        if (saved.algorithm) setAlgorithm(saved.algorithm as "classic" | "multifactor");
        if (typeof saved.min_score === "number") setMinScore(saved.min_score);
        if (saved.weights && typeof saved.weights === "object") {
          setWeights({ ...defaultW, ...(saved.weights as Record<string, number>) });
        } else {
          setWeights(defaultW);
        }
      }).catch(() => setWeights(defaultW));
    }).catch(() => {});
  }, []);

  const fetchStock = useCallback(
    (c: string, p: string) => {
      return api.stock(c, {
        period: PERIOD_MAP[p] || "daily",
        algorithm,
        min_score: 0,
        ...(algorithm === "multifactor" && Object.keys(weights).length > 0
          ? { factor_weights: weights }
          : {}),
      });
    },
    [algorithm, weights],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchStock(code, period)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, period, fetchStock]);

  const handleRefresh = () => {
    setRefreshing(true);
    api
      .refreshCandles(code, "latest")
      .then(() => fetchStock(code, period))
      .then((d) => setData(d))
      .catch(() => {})
      .finally(() => setRefreshing(false));
  };

  const handleWeightChange = (key: string, val: number) => {
    setWeights((prev) => {
      const next = { ...prev, [key]: val };
      // Debounced save to DB
      clearTimeout((window as any).__srSaveTimer);
      (window as any).__srSaveTimer = setTimeout(() => {
        api.putSetting("sr_config", { algorithm, weights: next, min_score: minScore }).catch(() => {});
      }, 800);
      return next;
    });
  };

  const handleMinScoreChange = (val: number) => {
    setMinScore(val);
    clearTimeout((window as any).__srScoreTimer);
    (window as any).__srScoreTimer = setTimeout(() => {
      api.putSetting("sr_config", { algorithm, weights, min_score: val }).catch(() => {});
    }, 800);
  };

  const handleAlgorithmChange = (algo: "classic" | "multifactor") => {
    setAlgorithm(algo);
    api.putSetting("sr_config", { algorithm: algo, weights, min_score: minScore }).catch(() => {});
  };

  return (
    <main
      className="grid flex-1"
      style={{ gridTemplateColumns: "280px 1fr 340px", minHeight: "calc(100vh - 84px)" }}
    >
      <WatchlistPanel activeCode={code} onSelect={onSelect} scanCounts={scanCounts} refreshKey={watchRefreshKey} />

      <div className="flex flex-col">
        <ChartWorkspace
          data={data}
          loading={loading}
          period={period}
          onPeriodChange={setPeriod}
          refreshing={refreshing}
          onRefresh={handleRefresh}
          isWatched={watchedCodes.has(code)}
          onToggleWatch={toggleWatch}
          minScore={minScore}
        />
        <ScreenerTable onSelect={onSelect} onResults={onScanResults} />
      </div>

      <aside className="border-l border-ink-700 bg-ink-900 flex flex-col overflow-y-auto scrollbar">
        <LevelsPanel levels={data?.levels ?? []} price={data?.quote.price ?? 0} />

        <div className="p-4 border-b border-ink-800">
          <div className="flex items-center justify-between mb-3">
            <span className="tag text-ink-500">算法配置</span>
          </div>
          {/* Algorithm selector */}
          <div className="flex gap-1.5 mb-3">
            <button
              className={"chip text-[11px] " + (algorithm === "multifactor" ? "chip-on ring-1 ring-gold/40" : "")}
              onClick={() => handleAlgorithmChange("multifactor")}
            >
              <i className="fas fa-brain mr-1 text-[10px]" />多因子
            </button>
            <button
              className={"chip text-[11px] " + (algorithm === "classic" ? "chip-on ring-1 ring-gold/40" : "")}
              onClick={() => handleAlgorithmChange("classic")}
            >
              <i className="fas fa-chart-line mr-1 text-[10px]" />经典
            </button>
          </div>

          {/* Min score filter */}
          <div className="mb-3">
            <div className="flex items-center justify-between mb-0.5">
              <span className="text-ink-300 text-[11px]" title="分数低于此阈值的支撑/阻力线将被过滤">最低分数过滤</span>
              <span className="num text-ink-400 text-[10px] w-8 text-right">{minScore}</span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={minScore}
              onChange={(e) => handleMinScoreChange(Number(e.target.value))}
              className="w-full accent-gold h-1"
            />
            <div className="flex justify-between text-[9px] text-ink-600 mt-0.5">
              <span>全部显示</span><span>仅强线</span>
            </div>
          </div>

          {algorithm === "multifactor" && factors.length > 0 && (
            <div className="space-y-2 text-[11px]">
              <div className="text-ink-500 text-[10px] mb-1">因子权重调节</div>
              {factors.map((f) => (
                <FactorSlider
                  key={f.key}
                  label={f.label}
                  value={weights[f.key] ?? f.default_weight}
                  onChange={(v) => handleWeightChange(f.key, v)}
                />
              ))}
              <button
                className="mt-2 w-full py-1.5 rounded-md bg-ink-850 ring-soft text-[11px] text-ink-300 hover:text-white"
                onClick={() => {
                  const w: Record<string, number> = {};
                  for (const f of factors) w[f.key] = f.default_weight;
                  setWeights(w);
                }}
              >
                <i className="fas fa-rotate-left mr-1 text-[10px]" />恢复默认权重
              </button>
            </div>
          )}

          {algorithm === "classic" && (
            <div className="space-y-3 text-[12px]">
              <ConfigSlider label="回看周期" value={120} suffix=" 日" min={30} max={240} />
              <ConfigSlider label="极值灵敏度" value={5} min={2} max={20} />
              <ConfigSlider label="价位聚类容差" value={1.2} suffix="%" min={0.1} max={5} step={0.1} />
            </div>
          )}
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

function FactorSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  // Extract short name (before " – ") from label
  const shortLabel = label.includes(" – ") ? label.split(" – ")[0] : label;
  const desc = label.includes(" – ") ? label.split(" – ")[1] : "";
  return (
    <div className="group">
      <div className="flex items-center justify-between mb-0.5">
        <span className="text-ink-300 text-[11px]" title={desc}>{shortLabel}</span>
        <span className="num text-ink-400 text-[10px] w-8 text-right">{value.toFixed(1)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={2}
        step={0.1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-gold h-1"
      />
    </div>
  );
}
