import { useEffect, useState, useCallback } from "react";
import { WatchlistPanel } from "../components/WatchlistPanel";
import { ChartWorkspace } from "../components/ChartWorkspace";
import { LevelsPanel } from "../components/LevelsPanel";
import { api } from "../services/api";
import type { StockDetail, SrFactor } from "../types";

export function WorkspacePage({
  code,
  onSelect,
  onAIAnalyze,
  pendingAnalyze,
  onConsumePending,
  strategyId,
  onStrategyConsumed,
}: {
  code: string;
  onSelect: (c: string) => void;
  onAIAnalyze?: (prompt: string, images?: string[]) => void;
  pendingAnalyze?: { code: string; extra: string } | null;
  onConsumePending?: () => void;
  strategyId?: number;
  onStrategyConsumed?: () => void;
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
      className="grid flex-1 overflow-hidden"
      style={{ gridTemplateColumns: "280px 1fr 340px", height: "calc(100vh - 84px)" }}
    >
      <WatchlistPanel activeCode={code} onSelect={onSelect} refreshKey={watchRefreshKey} />

      <div className="flex flex-col min-h-0 overflow-y-auto scrollbar">
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
          strategyId={strategyId}
          onStrategyConsumed={onStrategyConsumed}
          autoTriggerAI={!!(pendingAnalyze && pendingAnalyze.code === code && data && !loading)}
          onAutoTriggerConsumed={onConsumePending}
          onAIAnalyze={onAIAnalyze ? (_unused: string, imageData?: string) => {
            if (!data) return;
            const q = data.quote;
            const candles = data.candles;
            const levels = data.levels;
            // Build summary of 1-year K-line data
            const recent20 = candles.slice(-20);
            const high = Math.max(...candles.map(c => c.high));
            const low = Math.min(...candles.map(c => c.low));
            const avgVol = candles.length ? Math.round(candles.reduce((s, c) => s + c.volume, 0) / candles.length) : 0;
            const ma = (n: number) => {
              const s = candles.slice(-n);
              return s.length >= n ? (s.reduce((a, c) => a + c.close, 0) / n).toFixed(2) : "N/A";
            };
            const srText = levels.slice(0, 10).map(l => `${l.price.toFixed(2)}(${l.kind},得分${l.score ?? "-"})`).join(", ");
            const recentText = recent20.map(c => `${c.date}:O${c.open} H${c.high} L${c.low} C${c.close} V${c.volume}`).join("\n");
            // Fundamentals & concepts
            const f = q.fundamentals;
            const fundText = f ? `EPS(TTM)=${f.eps_ttm ?? "N/A"}, ROE=${f.roe ?? "N/A"}, 营收同比=${f.revenue_yoy ?? "N/A"}%, 净利同比=${f.net_profit_yoy ?? "N/A"}%, ${f.fundamental_summary ?? ""}` : "无";
            const conceptsText = q.concepts?.join(", ") || "无";
            const ac = q.analyst_consensus;
            const analystText = ac ? `目标价=${ac.consensus_target ?? "N/A"}, 分析师${ac.analyst_count}人, 买入${ac.buy_count}/增持${ac.overweight_count}/中性${ac.neutral_count}` : "无";
            const prompt = `请对以下股票进行深度技术分析和投资建议：

【基本信息】
代码: ${q.code} | 名称: ${q.name} | 行业: ${q.industry || "未知"}
现价: ${q.price} | 涨跌幅: ${q.change_pct}%

【K线概览】(共${candles.length}根K线)
区间最高: ${high} | 区间最低: ${low}
MA10=${ma(10)} | MA20=${ma(20)} | MA50=${ma(50)} | MA120=${ma(120)} | MA250=${ma(250)}
平均成交量: ${avgVol}

【近20日明细】
${recentText}

【关键支撑/压力位】
${srText || "无"}

【基本面】
${fundText}

【概念板块】${conceptsText}
【机构共识】${analystText}

${imageData ? "上方附有该股票的K线截图，请结合图表形态一并分析。\n\n" : ""}${
  pendingAnalyze && pendingAnalyze.code === code && pendingAnalyze.extra
    ? `\n\u3010选股器信号】\n${pendingAnalyze.extra}\n\n`
    : ""
}请分析：1)当前趋势和位置 2)关键支撑压力位分析 3)量价关系 4)基本面评估 5)综合建议和风险提示`;
            const images = imageData ? [imageData] : undefined;
            onAIAnalyze(prompt, images);
          } : undefined}
        />
      </div>

      <aside className="border-l border-ink-700 bg-ink-900 flex flex-col overflow-y-auto scrollbar min-h-0">
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
