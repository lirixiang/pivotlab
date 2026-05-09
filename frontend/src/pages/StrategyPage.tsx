import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import { LabelChart } from "../components/LabelChart";
import { TradeChart } from "../components/TradeChart";
import type {
  AiBacktestResult,
  AiModelStatus,
  AiSignal,
  AiTrainResult,
  Candle,
  LabeledPoint,
  RegimeFitResult,
  PatternResult,
} from "../types";

type ModelType = "lightgbm" | "transformer" | "lstm" | "cnn_lstm" | "rl_ppo" | "ensemble";
type Tab = "train" | "signal" | "backtest" | "scan" | "regime" | "position" | "pattern";

export function StrategyPage({ defaultCode }: { defaultCode: string }) {
  const [tab, setTab] = useState<Tab>("train");
  const [code, setCode] = useState(defaultCode);
  const [modelType, setModelType] = useState<ModelType>("lightgbm");
  const [status, setStatus] = useState<AiModelStatus | null>(null);

  // Load status on mount
  useEffect(() => {
    api.aiStatus().then(setStatus).catch(() => {});
  }, []);

  useEffect(() => { setCode(defaultCode); }, [defaultCode]);

  const tabs: { k: Tab; l: string; icon: string }[] = [
    { k: "train", l: "训练模型", icon: "fa-graduation-cap" },
    { k: "signal", l: "信号预测", icon: "fa-crosshairs" },
    { k: "backtest", l: "AI回测", icon: "fa-chart-line" },
    { k: "scan", l: "AI选股", icon: "fa-radar" },
    { k: "regime", l: "市场状态", icon: "fa-chart-area" },
    { k: "position", l: "RL仓位", icon: "fa-robot" },
    { k: "pattern", l: "形态识别", icon: "fa-shapes" },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-4 px-5 py-3 border-b border-ink-800">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center">
            <i className="fas fa-brain text-white text-sm" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-white">策略引擎</h2>
            <p className="text-[10px] text-ink-500 -mt-0.5 tracking-wide">信号训练 · 市场感知 · 仓位管理 · 形态识别</p>
          </div>
        </div>
        <div className="flex items-center gap-1 ml-4">
          {tabs.map((t) => (
            <button
              key={t.k}
              onClick={() => setTab(t.k)}
              className={
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition " +
                (tab === t.k ? "bg-ink-800 text-white ring-soft" : "text-ink-500 hover:text-ink-200")
              }
            >
              <i className={`fas ${t.icon} text-[10px]`} />
              {t.l}
            </button>
          ))}
        </div>
        {/* Model selector */}
        <div className="ml-auto flex items-center gap-2 text-xs">
          <span className="text-ink-500">模型:</span>
          <button onClick={() => setModelType("lightgbm")} className={"px-2 py-1 rounded " + (modelType === "lightgbm" ? "bg-green-900/40 text-green-400 ring-1 ring-green-700" : "text-ink-500 hover:text-ink-200")}>
            LightGBM
          </button>
          <button onClick={() => setModelType("transformer")} className={"px-2 py-1 rounded " + (modelType === "transformer" ? "bg-purple-900/40 text-purple-400 ring-1 ring-purple-700" : "text-ink-500 hover:text-ink-200")}>
            Transformer
          </button>
          <button onClick={() => setModelType("lstm")} className={"px-2 py-1 rounded " + (modelType === "lstm" ? "bg-blue-900/40 text-blue-400 ring-1 ring-blue-700" : "text-ink-500 hover:text-ink-200")}>
            LSTM
          </button>
          <button onClick={() => setModelType("cnn_lstm")} className={"px-2 py-1 rounded " + (modelType === "cnn_lstm" ? "bg-amber-900/40 text-amber-400 ring-1 ring-amber-700" : "text-ink-500 hover:text-ink-200")}>
            CNN-LSTM
          </button>
          <button onClick={() => setModelType("rl_ppo")} className={"px-2 py-1 rounded " + (modelType === "rl_ppo" ? "bg-cyan-900/40 text-cyan-400 ring-1 ring-cyan-700" : "text-ink-500 hover:text-ink-200")}>
            RL-PPO
          </button>
          <button onClick={() => setModelType("ensemble")} className={"px-2 py-1 rounded " + (modelType === "ensemble" ? "bg-rose-900/40 text-rose-400 ring-1 ring-rose-700" : "text-ink-500 hover:text-ink-200")}>
            🎯 集成
          </button>
          {/* Status dots */}
          {status && (
            <div className="flex items-center gap-2 ml-3">
              <span className="flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${status.lightgbm?.trained ? "bg-green-500" : "bg-ink-600"}`} />
                <span className="text-[10px] text-ink-500">LGB</span>
              </span>
              <span className="flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${status.transformer?.trained ? "bg-purple-500" : "bg-ink-600"}`} />
                <span className="text-[10px] text-ink-500">TRF</span>
              </span>
              <span className="flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${status.lstm?.trained ? "bg-blue-500" : "bg-ink-600"}`} />
                <span className="text-[10px] text-ink-500">LSTM</span>
              </span>
              <span className="flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${status.cnn_lstm?.trained ? "bg-amber-500" : "bg-ink-600"}`} />
                <span className="text-[10px] text-ink-500">CNN</span>
              </span>
              <span className="flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${status.rl_ppo?.trained ? "bg-cyan-500" : "bg-ink-600"}`} />
                <span className="text-[10px] text-ink-500">RL</span>
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {tab === "train" && (
          <TrainPanel
            code={code}
            setCode={setCode}
            modelType={modelType}
            onTrained={() => api.aiStatus().then(setStatus).catch(() => {})}
          />
        )}
        {tab === "signal" && (
          <SignalPanel code={code} setCode={setCode} modelType={modelType} status={status} />
        )}
        {tab === "backtest" && (
          <BacktestPanel code={code} setCode={setCode} modelType={modelType} status={status} />
        )}
        {tab === "scan" && (
          <ScanPanel code={code} modelType={modelType} status={status} setCode={setCode} />
        )}
        {tab === "regime" && <RegimePanel />}
        {tab === "position" && <RlPositionPanel />}
        {tab === "pattern" && <PatternPanel />}
      </div>
    </div>
  );
}

/* ─────────────────────── Train Panel ─────────────────────── */

function TrainPanel({
  code,
  setCode,
  modelType,
  onTrained,
}: {
  code: string;
  setCode: (c: string) => void;
  modelType: ModelType;
  onTrained: () => void;
}) {
  const [codes, setCodes] = useState(code);
  const [scope, setScope] = useState<"single" | "industry">("single");
  const [industryInfo, setIndustryInfo] = useState<{ industry: string; stocks: { code: string; name: string }[] } | null>(null);
  const [industryLoading, setIndustryLoading] = useState(false);
  const [labelMethod, setLabelMethod] = useState("zigzag");
  const [pctThreshold, setPctThreshold] = useState(5);
  const [epochs, setEpochs] = useState(50);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AiTrainResult | null>(null);
  const [labels, setLabels] = useState<LabeledPoint[]>([]);
  const [labelsCandles, setLabelsCandles] = useState<Candle[]>([]);
  const [labelsCode, setLabelsCode] = useState("");
  const [labelsLoading, setLabelsLoading] = useState(false);

  // When scope changes to "industry", fetch industry stocks
  const fetchIndustry = useCallback((inputCode: string) => {
    const c = inputCode.split(/[,，\s]+/)[0]?.trim();
    if (!c) return;
    setIndustryLoading(true);
    api.aiIndustryStocks(c)
      .then((r) => {
        setIndustryInfo(r);
        if (r.stocks.length > 0) {
          setCodes(r.stocks.map((s) => s.code).join(","));
        }
      })
      .catch(() => setIndustryInfo(null))
      .finally(() => setIndustryLoading(false));
  }, []);

  useEffect(() => {
    if (scope === "industry") {
      fetchIndustry(codes);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const handleScopeChange = useCallback((newScope: "single" | "industry") => {
    setScope(newScope);
    if (newScope === "single") {
      setCodes(code);
      setIndustryInfo(null);
    }
  }, [code]);

  const previewLabels = useCallback(() => {
    const c = codes.split(/[,，\s]+/)[0]?.trim();
    if (!c) return;
    setLabelsLoading(true);
    api.aiLabels(c, { method: labelMethod, pct_threshold: pctThreshold })
      .then((r) => { setLabels(r.points); setLabelsCandles(r.candles); setLabelsCode(c); })
      .catch(() => {})
      .finally(() => setLabelsLoading(false));
  }, [codes, labelMethod, pctThreshold]);

  const train = useCallback(() => {
    const codeList = codes.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    if (!codeList.length) return;
    setLoading(true);
    setResult(null);
    api.aiTrain({
      codes: codeList,
      model_type: modelType,
      label_method: labelMethod,
      pct_threshold: pctThreshold,
      epochs,
    })
      .then((r) => { setResult(r); onTrained(); })
      .catch((e) => setResult({ error: e.message } as AiTrainResult))
      .finally(() => setLoading(false));
  }, [codes, modelType, labelMethod, pctThreshold, epochs, onTrained]);

  return (
    <div className="flex gap-4 p-5">
      {/* Left: config */}
      <div className="w-80 space-y-4 shrink-0">
        <div className="bg-ink-900 rounded-xl border border-ink-800 p-4 space-y-3">
          <h3 className="text-xs font-semibold text-ink-300 flex items-center gap-2">
            <i className="fas fa-database text-[10px]" /> 训练配置
          </h3>

          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">训练范围</label>
            <div className="flex gap-1.5">
              <button
                className={`flex-1 text-[11px] py-1.5 rounded-lg border transition-colors ${
                  scope === "single"
                    ? "bg-amber-500/20 border-amber-500/50 text-amber-400"
                    : "border-ink-700 text-ink-400 hover:border-ink-600"
                }`}
                onClick={() => handleScopeChange("single")}
              >
                <i className="fas fa-crosshairs mr-1 text-[9px]" /> 自选股票
              </button>
              <button
                className={`flex-1 text-[11px] py-1.5 rounded-lg border transition-colors ${
                  scope === "industry"
                    ? "bg-blue-500/20 border-blue-500/50 text-blue-400"
                    : "border-ink-700 text-ink-400 hover:border-ink-600"
                }`}
                onClick={() => handleScopeChange("industry")}
              >
                <i className="fas fa-industry mr-1 text-[9px]" /> 同行业
              </button>
            </div>
          </div>

          {scope === "industry" && industryInfo && (
            <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-2.5">
              <div className="text-[11px] text-blue-400 font-medium mb-1.5">
                <i className="fas fa-sitemap mr-1" />
                {industryInfo.industry} ({industryInfo.stocks.length}只)
              </div>
              <div className="flex flex-wrap gap-1 max-h-20 overflow-auto">
                {industryInfo.stocks.map((s) => (
                  <span key={s.code} className="text-[10px] bg-ink-800 text-ink-300 px-1.5 py-0.5 rounded">
                    {s.name}
                  </span>
                ))}
              </div>
            </div>
          )}
          {scope === "industry" && industryLoading && (
            <div className="text-[11px] text-ink-500">
              <i className="fas fa-spinner fa-spin mr-1" /> 加载行业数据...
            </div>
          )}

          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">
              {scope === "single" ? "股票代码 (逗号分隔)" : "训练股票 (已自动填入)"}
            </label>
            <textarea
              className="inp w-full text-xs h-16 resize-none"
              value={codes}
              onChange={(e) => setCodes(e.target.value)}
              placeholder="600519,000001,000858..."
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">标注方法</label>
              <select
                className="inp w-full text-xs"
                value={labelMethod}
                onChange={(e) => setLabelMethod(e.target.value)}
              >
                <option value="zigzag">ZigZag (经典)</option>
                <option value="dp">动态规划 (最优)</option>
              </select>
            </div>
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">阈值 %</label>
              <input
                type="number"
                className="inp w-full text-xs"
                value={pctThreshold}
                onChange={(e) => setPctThreshold(Number(e.target.value))}
                step={0.5}
                min={1}
                max={20}
              />
            </div>
          </div>

          {modelType === "transformer" && (
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">训练轮数 (Epochs)</label>
              <input
                type="number"
                className="inp w-full text-xs"
                value={epochs}
                onChange={(e) => setEpochs(Number(e.target.value))}
                min={10}
                max={200}
              />
            </div>
          )}

          <div className="flex gap-2">
            <button
              className="btn-gold flex-1 text-xs py-2"
              onClick={train}
              disabled={loading}
            >
              {loading ? (
                <><i className="fas fa-spinner fa-spin mr-1" /> 训练中...</>
              ) : (
                <><i className="fas fa-play mr-1" /> 开始训练</>
              )}
            </button>
            <button
              className="btn-outline text-xs px-3"
              onClick={previewLabels}
              disabled={labelsLoading}
            >
              <i className="fas fa-eye mr-1" />
              预览标注
            </button>
          </div>
        </div>

        {/* Model info */}
        <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
          <h3 className="text-xs font-semibold text-ink-300 mb-2">
            {modelType === "lightgbm" ? "LightGBM" : "Transformer"} 模型说明
          </h3>
          {modelType === "lightgbm" ? (
            <ul className="text-[11px] text-ink-500 space-y-1">
              <li>• 梯度提升决策树，31维技术特征</li>
              <li>• 多分类: hold / buy / sell</li>
              <li>• 训练快 (CPU 几秒)，可解释性强</li>
              <li>• 输出特征重要性排名</li>
            </ul>
          ) : (
            <ul className="text-[11px] text-ink-500 space-y-1">
              <li>• 时序 Transformer，30步窗口</li>
              <li>• 多头自注意力，捕捉时序模式</li>
              <li>• GPU 加速 (RTX 3080 Ti)</li>
              <li>• 位置编码 + 余弦退火学习率</li>
            </ul>
          )}
        </div>

        {/* Market training */}
        <MarketTrainPanel modelType={modelType} onTrained={onTrained} />
      </div>

      {/* Right: results */}
      <div className="flex-1 space-y-4">
        {/* Label chart visualization */}
        {labels.length > 0 && labelsCandles.length > 0 && (
          <LabelChart candles={labelsCandles} labels={labels} code={labelsCode} />
        )}

        {/* Label table */}
        {labels.length > 0 && (
          <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
            <h3 className="text-xs font-semibold text-ink-300 mb-3">
              <i className="fas fa-tags mr-1" /> {labelsCode} 标注明细 ({labels.length}个)
            </h3>
            <div className="max-h-64 overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-ink-500 border-b border-ink-800">
                    <th className="text-left py-1.5 px-2">日期</th>
                    <th className="text-right py-1.5 px-2">价格</th>
                    <th className="text-center py-1.5 px-2">信号</th>
                  </tr>
                </thead>
                <tbody>
                  {labels.map((p, i) => (
                    <tr key={i} className="border-b border-ink-800/50 hover:bg-ink-850">
                      <td className="py-1.5 px-2 text-ink-300 num">{p.date}</td>
                      <td className="py-1.5 px-2 text-right num">{p.price.toFixed(2)}</td>
                      <td className="py-1.5 px-2 text-center">
                        <span
                          className={
                            "text-[10px] px-2 py-0.5 rounded-full font-medium " +
                            (p.label === "buy"
                              ? "bg-green-900/40 text-green-400"
                              : "bg-red-900/40 text-red-400")
                          }
                        >
                          {p.label === "buy" ? "▲ 买" : "▼ 卖"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Training result */}
        {result && (
          <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
            {result.error ? (
              <div className="text-red-400 text-sm">
                <i className="fas fa-exclamation-triangle mr-2" />
                {result.error}
              </div>
            ) : (
              <>
                <div className="flex items-center gap-3 mb-4">
                  <h3 className="text-xs font-semibold text-ink-300">
                    <i className="fas fa-check-circle text-green-500 mr-1" />
                    训练完成
                  </h3>
                  <span className="text-[10px] text-ink-500">
                    {result.model} · {result.codes_used} 只股票 · {result.samples} 样本 · {result.elapsed_sec}秒
                    {result.device && ` · ${result.device}`}
                  </span>
                </div>

                {/* Metrics grid */}
                <div className="grid grid-cols-5 gap-3 mb-4">
                  <MetricCard label="准确率" value={`${(result.accuracy * 100).toFixed(1)}%`} color="blue" />
                  <MetricCard label="买入精确率" value={`${(result.buy_precision * 100).toFixed(1)}%`} color="green" />
                  <MetricCard label="买入召回率" value={`${(result.buy_recall * 100).toFixed(1)}%`} color="green" />
                  <MetricCard label="卖出精确率" value={`${(result.sell_precision * 100).toFixed(1)}%`} color="red" />
                  <MetricCard label="卖出召回率" value={`${(result.sell_recall * 100).toFixed(1)}%`} color="red" />
                </div>

                {/* Class distribution */}
                {result.class_counts && (
                  <div className="flex items-center gap-4 mb-4 text-[11px]">
                    <span className="text-ink-500">样本分布:</span>
                    <span className="text-ink-400">
                      Hold {result.class_counts["0"] ?? 0}
                    </span>
                    <span className="text-green-400">
                      Buy {result.class_counts["1"] ?? 0}
                    </span>
                    <span className="text-red-400">
                      Sell {result.class_counts["2"] ?? 0}
                    </span>
                  </div>
                )}

                {/* Feature importance (LGB only) */}
                {result.feature_importance && (
                  <div>
                    <h4 className="text-[11px] text-ink-500 mb-2">特征重要性 Top 10</h4>
                    <div className="space-y-1">
                      {Object.entries(result.feature_importance)
                        .slice(0, 10)
                        .map(([k, v]) => {
                          const max = Math.max(...Object.values(result.feature_importance!));
                          const pct = (v / max) * 100;
                          return (
                            <div key={k} className="flex items-center gap-2 text-[11px]">
                              <span className="w-28 text-ink-400 truncate">{k}</span>
                              <div className="flex-1 h-2 bg-ink-800 rounded-full overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-gradient-to-r from-blue-600 to-blue-400"
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                              <span className="w-12 text-right num text-ink-500">
                                {v.toFixed(0)}
                              </span>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────── Signal Panel ─────────────────────── */

function SignalPanel({
  code,
  setCode,
  modelType,
  status,
}: {
  code: string;
  setCode: (c: string) => void;
  modelType: ModelType;
  status: AiModelStatus | null;
}) {
  const [inputCode, setInputCode] = useState(code);
  const [signal, setSignal] = useState<AiSignal | null>(null);
  const [loading, setLoading] = useState(false);
  const [buyThreshold, setBuyThreshold] = useState(0.4);
  const [sellThreshold, setSellThreshold] = useState(0.4);

  const fetch = useCallback(() => {
    const c = inputCode.trim();
    if (!c) return;
    setCode(c);
    setLoading(true);
    api.aiSignal(c, { model_type: modelType, buy_threshold: buyThreshold, sell_threshold: sellThreshold })
      .then(setSignal)
      .catch((e) => setSignal({ error: e.message } as AiSignal))
      .finally(() => setLoading(false));
  }, [inputCode, modelType, buyThreshold, sellThreshold, setCode]);

  const trained = modelType === "ensemble"
    ? Object.values(status ?? {}).filter((v: any) => v?.trained).length >= 2
    : (status as any)?.[modelType]?.trained;

  return (
    <div className="flex gap-4 p-5">
      {/* Left config */}
      <div className="w-72 space-y-4 shrink-0">
        <div className="bg-ink-900 rounded-xl border border-ink-800 p-4 space-y-3">
          <h3 className="text-xs font-semibold text-ink-300">
            <i className="fas fa-crosshairs mr-1" /> 信号预测
          </h3>
          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">股票代码</label>
            <input
              className="inp w-full text-xs"
              value={inputCode}
              onChange={(e) => setInputCode(e.target.value)}
              placeholder="600519"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">买入阈值</label>
              <input
                type="number"
                className="inp w-full text-xs"
                value={buyThreshold}
                onChange={(e) => setBuyThreshold(Number(e.target.value))}
                step={0.05}
                min={0.1}
                max={0.9}
              />
            </div>
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">卖出阈值</label>
              <input
                type="number"
                className="inp w-full text-xs"
                value={sellThreshold}
                onChange={(e) => setSellThreshold(Number(e.target.value))}
                step={0.05}
                min={0.1}
                max={0.9}
              />
            </div>
          </div>
          <button
            className="btn-gold w-full text-xs py-2"
            onClick={fetch}
            disabled={loading || !trained}
          >
            {loading ? (
              <><i className="fas fa-spinner fa-spin mr-1" /> 预测中...</>
            ) : !trained ? (
              <><i className="fas fa-lock mr-1" /> 请先训练模型</>
            ) : (
              <><i className="fas fa-bolt mr-1" /> 获取信号</>
            )}
          </button>
        </div>
      </div>

      {/* Right: signal card */}
      <div className="flex-1 space-y-4">
        {signal && !signal.error && signal.candles && signal.candles.length > 0 && (
          <TradeChart
            candles={signal.candles}
            markers={
              signal.action !== "hold"
                ? [{ date: signal.candles[signal.candles.length - 1].date, type: signal.action as "buy" | "sell", price: signal.entry_price }]
                : []
            }
            title={`${signal.code} 信号预测`}
            hlines={[
              ...(signal.stop_loss > 0 ? [{ price: signal.stop_loss, color: "#ef4444", label: "止损", dash: true }] : []),
              ...(signal.target_price > 0 ? [{ price: signal.target_price, color: "#22c55e", label: "目标", dash: true }] : []),
              ...(signal.entry_price > 0 && signal.action !== "hold" ? [{ price: signal.entry_price, color: "#60a5fa", label: "入场", dash: false }] : []),
            ]}
          />
        )}
        {signal && !signal.error && <AiSignalCard signal={signal} />}
        {signal?.error && (
          <div className="bg-ink-900 rounded-xl border border-ink-800 p-6 text-red-400 text-sm">
            <i className="fas fa-exclamation-triangle mr-2" />
            {signal.error}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────── Backtest Panel ─────────────────────── */

function BacktestPanel({
  code,
  setCode,
  modelType,
  status,
}: {
  code: string;
  setCode: (c: string) => void;
  modelType: ModelType;
  status: AiModelStatus | null;
}) {
  const [inputCode, setInputCode] = useState(code);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AiBacktestResult | null>(null);
  const [buyThreshold, setBuyThreshold] = useState(0.4);
  const [sellThreshold, setSellThreshold] = useState(0.4);
  const [stopMult, setStopMult] = useState(2.0);
  const [targetMult, setTargetMult] = useState(3.0);
  const [maxHold, setMaxHold] = useState(20);

  const run = useCallback(() => {
    const c = inputCode.trim();
    if (!c) return;
    setCode(c);
    setLoading(true);
    setResult(null);
    api.aiBacktest({
      code: c,
      model_type: modelType,
      buy_threshold: buyThreshold,
      sell_threshold: sellThreshold,
      stop_atr_mult: stopMult,
      target_atr_mult: targetMult,
      max_hold_bars: maxHold,
    })
      .then(setResult)
      .catch((e) => setResult({ error: e.message } as AiBacktestResult))
      .finally(() => setLoading(false));
  }, [inputCode, modelType, buyThreshold, sellThreshold, stopMult, targetMult, maxHold, setCode]);

  const trained = modelType === "ensemble"
    ? Object.values(status ?? {}).filter((v: any) => v?.trained).length >= 2
    : (status as any)?.[modelType]?.trained;

  return (
    <div className="flex gap-4 p-5">
      {/* Left config */}
      <div className="w-72 space-y-4 shrink-0">
        <div className="bg-ink-900 rounded-xl border border-ink-800 p-4 space-y-3">
          <h3 className="text-xs font-semibold text-ink-300">
            <i className="fas fa-chart-line mr-1" /> AI 回测
          </h3>
          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">股票代码</label>
            <input
              className="inp w-full text-xs"
              value={inputCode}
              onChange={(e) => setInputCode(e.target.value)}
              placeholder="600519"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">买入阈值</label>
              <input type="number" className="inp w-full text-xs" value={buyThreshold}
                onChange={(e) => setBuyThreshold(Number(e.target.value))} step={0.05} min={0.1} max={0.9} />
            </div>
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">卖出阈值</label>
              <input type="number" className="inp w-full text-xs" value={sellThreshold}
                onChange={(e) => setSellThreshold(Number(e.target.value))} step={0.05} min={0.1} max={0.9} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">止损 ATR倍</label>
              <input type="number" className="inp w-full text-xs" value={stopMult}
                onChange={(e) => setStopMult(Number(e.target.value))} step={0.5} min={0.5} max={5} />
            </div>
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">止盈 ATR倍</label>
              <input type="number" className="inp w-full text-xs" value={targetMult}
                onChange={(e) => setTargetMult(Number(e.target.value))} step={0.5} min={1} max={10} />
            </div>
          </div>
          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">最大持仓天数</label>
            <input type="number" className="inp w-full text-xs" value={maxHold}
              onChange={(e) => setMaxHold(Number(e.target.value))} min={5} max={60} />
          </div>
          <button
            className="btn-gold w-full text-xs py-2"
            onClick={run}
            disabled={loading || !trained}
          >
            {loading ? (
              <><i className="fas fa-spinner fa-spin mr-1" /> 回测中...</>
            ) : !trained ? (
              <><i className="fas fa-lock mr-1" /> 请先训练模型</>
            ) : (
              <><i className="fas fa-play mr-1" /> 运行回测</>
            )}
          </button>
        </div>
      </div>

      {/* Right: results */}
      <div className="flex-1 space-y-4">
        {result?.error && (
          <div className="bg-ink-900 rounded-xl border border-ink-800 p-6 text-red-400 text-sm">
            <i className="fas fa-exclamation-triangle mr-2" />{result.error}
          </div>
        )}

        {result && !result.error && result.stats && (
          <>
            {/* Stats cards */}
            <div className="grid grid-cols-6 gap-3">
              <MetricCard label="总交易" value={`${result.stats.total_trades}`} color="blue" />
              <MetricCard label="胜率" value={`${(result.stats.win_rate * 100).toFixed(1)}%`}
                color={result.stats.win_rate >= 0.5 ? "green" : "red"} />
              <MetricCard label="总收益" value={`${result.stats.total_return.toFixed(1)}%`}
                color={result.stats.total_return >= 0 ? "green" : "red"} />
              <MetricCard label="基准收益" value={`${result.stats.benchmark_return.toFixed(1)}%`} color="blue" />
              <MetricCard label="最大回撤" value={`${result.stats.max_drawdown.toFixed(1)}%`} color="red" />
              <MetricCard label="Sharpe" value={`${result.stats.sharpe.toFixed(2)}`}
                color={result.stats.sharpe >= 1 ? "green" : "yellow"} />
            </div>

            {/* Extra stats */}
            <div className="grid grid-cols-4 gap-3">
              <MetricCard label="盈利笔数" value={`${result.stats.win_count}`} color="green" />
              <MetricCard label="亏损笔数" value={`${result.stats.loss_count}`} color="red" />
              <MetricCard label="平均盈利" value={`${result.stats.avg_win.toFixed(1)}%`} color="green" />
              <MetricCard label="平均亏损" value={`${result.stats.avg_loss.toFixed(1)}%`} color="red" />
            </div>

            {/* Trade chart */}
            {result.candles && result.candles.length > 0 && result.trades.length > 0 && (
              <TradeChart
                candles={result.candles}
                markers={result.trades.flatMap((t) => [
                  { date: t.entry_date, type: "buy" as const, price: t.entry_price },
                  { date: t.exit_date, type: "sell" as const, price: t.exit_price },
                ])}
                title={`${result.code} AI回测交易 (${result.trades.length}笔)`}
              />
            )}

            {/* Equity curve */}
            {result.equity_curve.length > 0 && (
              <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
                <h3 className="text-xs font-semibold text-ink-300 mb-3">
                  <i className="fas fa-chart-area mr-1" /> 净值曲线
                </h3>
                <EquityCurve data={result.equity_curve} />
              </div>
            )}

            {/* Trade list */}
            {result.trades.length > 0 && (
              <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
                <h3 className="text-xs font-semibold text-ink-300 mb-3">
                  <i className="fas fa-list mr-1" /> 交易明细 ({result.trades.length}笔)
                </h3>
                <div className="max-h-80 overflow-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-ink-500 border-b border-ink-800">
                        <th className="text-left py-1.5 px-2">买入日</th>
                        <th className="text-right py-1.5 px-2">买入价</th>
                        <th className="text-left py-1.5 px-2">卖出日</th>
                        <th className="text-right py-1.5 px-2">卖出价</th>
                        <th className="text-right py-1.5 px-2">净盈亏</th>
                        <th className="text-right py-1.5 px-2">持仓</th>
                        <th className="text-left py-1.5 px-2">原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.trades.map((t, i) => (
                        <tr key={i} className="border-b border-ink-800/50 hover:bg-ink-850">
                          <td className="py-1.5 px-2 num text-ink-300">{t.entry_date}</td>
                          <td className="py-1.5 px-2 text-right num">{t.entry_price.toFixed(2)}</td>
                          <td className="py-1.5 px-2 num text-ink-300">{t.exit_date}</td>
                          <td className="py-1.5 px-2 text-right num">{t.exit_price.toFixed(2)}</td>
                          <td className={`py-1.5 px-2 text-right num font-medium ${t.pnl_net >= 0 ? "text-cn-up" : "text-cn-dn"}`}>
                            {t.pnl_net >= 0 ? "+" : ""}{t.pnl_net.toFixed(2)}%
                          </td>
                          <td className="py-1.5 px-2 text-right num text-ink-500">{t.holding_bars}天</td>
                          <td className="py-1.5 px-2 text-ink-500 truncate max-w-[150px]">{t.reason_exit}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────── Shared Components ─────────────────────── */

function MetricCard({ label, value, color }: { label: string; value: string; color: string }) {
  const colorMap: Record<string, string> = {
    green: "text-green-400",
    red: "text-red-400",
    blue: "text-blue-400",
    yellow: "text-yellow-400",
    purple: "text-purple-400",
  };
  return (
    <div className="bg-ink-850 rounded-lg border border-ink-800 p-3 text-center">
      <div className={`text-lg font-bold num ${colorMap[color] || "text-ink-200"}`}>
        {value}
      </div>
      <div className="text-[10px] text-ink-500 mt-0.5">{label}</div>
    </div>
  );
}

function AiSignalCard({ signal: s }: { signal: AiSignal }) {
  const actionStyle =
    s.action === "buy"
      ? "bg-green-900/40 text-green-400 ring-green-700"
      : s.action === "sell"
        ? "bg-red-900/40 text-red-400 ring-red-700"
        : "bg-ink-800 text-ink-400 ring-ink-700";

  const actionLabel =
    s.action === "buy" ? "▲ 买入" : s.action === "sell" ? "▼ 卖出" : "● 观望";

  return (
    <div className="bg-ink-900 rounded-xl border border-ink-800 p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className={`px-3 py-1 rounded-full text-sm font-bold ring-1 ${actionStyle}`}>
          {actionLabel}
        </span>
        <span className="text-ink-400 text-xs">{s.code}</span>
        <span className="text-ink-500 text-[10px] ml-auto">{s.model_type}</span>
      </div>

      {/* Reason */}
      <p className="text-sm text-ink-300">{s.reason}</p>

      {/* Confidence bar */}
      <div>
        <div className="flex justify-between text-[11px] mb-1">
          <span className="text-ink-500">置信度</span>
          <span className="text-ink-300 font-medium">{s.confidence.toFixed(0)}%</span>
        </div>
        <div className="h-2 bg-ink-800 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              s.confidence >= 60 ? "bg-green-500" : s.confidence >= 30 ? "bg-yellow-500" : "bg-ink-600"
            }`}
            style={{ width: `${s.confidence}%` }}
          />
        </div>
      </div>

      {/* Probabilities */}
      {s.probabilities && (
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div className="bg-ink-850 rounded-lg p-2">
            <div className="text-ink-500 text-[10px]">持有</div>
            <div className="num text-ink-300">{(s.probabilities.hold * 100).toFixed(1)}%</div>
          </div>
          <div className="bg-ink-850 rounded-lg p-2">
            <div className="text-green-500 text-[10px]">买入</div>
            <div className="num text-green-400">{(s.probabilities.buy * 100).toFixed(1)}%</div>
          </div>
          <div className="bg-ink-850 rounded-lg p-2">
            <div className="text-red-500 text-[10px]">卖出</div>
            <div className="num text-red-400">{(s.probabilities.sell * 100).toFixed(1)}%</div>
          </div>
        </div>
      )}

      {/* Prices */}
      <div className="grid grid-cols-4 gap-3">
        <PriceCell label="当前价" value={s.current_price} />
        <PriceCell label="入场价" value={s.entry_price} color="blue" />
        <PriceCell label="止损价" value={s.stop_loss} color="red" />
        <PriceCell label="目标价" value={s.target_price} color="green" />
      </div>

      {/* Risk/Reward */}
      <div className="grid grid-cols-4 gap-3 text-xs">
        <div className="bg-ink-850 rounded-lg p-2 text-center">
          <div className="text-[10px] text-ink-500">风险</div>
          <div className="num text-red-400">{s.risk_pct.toFixed(2)}%</div>
        </div>
        <div className="bg-ink-850 rounded-lg p-2 text-center">
          <div className="text-[10px] text-ink-500">收益</div>
          <div className="num text-green-400">{s.reward_pct.toFixed(2)}%</div>
        </div>
        <div className="bg-ink-850 rounded-lg p-2 text-center">
          <div className="text-[10px] text-ink-500">风险回报</div>
          <div className="num text-ink-200">{s.risk_reward.toFixed(2)}</div>
        </div>
        <div className="bg-ink-850 rounded-lg p-2 text-center">
          <div className="text-[10px] text-ink-500">建议仓位</div>
          <div className="num text-ink-200">{s.suggested_position_pct.toFixed(1)}%</div>
        </div>
      </div>

      {/* Factors */}
      {s.factors.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {s.factors.map((f, i) => (
            <span key={i} className="chip text-[10px]">{f}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function PriceCell({ label, value, color }: { label: string; value: number; color?: string }) {
  const c = color === "green" ? "text-green-400" : color === "red" ? "text-red-400" : color === "blue" ? "text-blue-400" : "text-ink-200";
  return (
    <div className="bg-ink-850 rounded-lg p-2 text-center">
      <div className="text-[10px] text-ink-500">{label}</div>
      <div className={`num text-sm font-medium ${c}`}>{value.toFixed(2)}</div>
    </div>
  );
}

function EquityCurve({ data }: { data: { date: string; equity: number; benchmark: number }[] }) {
  if (data.length < 2) return null;

  const W = 800;
  const H = 200;
  const pad = { t: 10, r: 10, b: 25, l: 50 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const eqs = data.map((d) => d.equity);
  const bms = data.map((d) => d.benchmark);
  const allVals = [...eqs, ...bms];
  const mn = Math.min(...allVals);
  const mx = Math.max(...allVals);
  const range = mx - mn || 1;

  const toX = (i: number) => pad.l + (i / (data.length - 1)) * iw;
  const toY = (v: number) => pad.t + (1 - (v - mn) / range) * ih;

  const eqPath = eqs.map((v, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");
  const bmPath = bms.map((v, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");

  // Y axis labels
  const yTicks = [mn, mn + range / 2, mx];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Grid */}
      {yTicks.map((v, i) => (
        <g key={i}>
          <line x1={pad.l} x2={W - pad.r} y1={toY(v)} y2={toY(v)} stroke="#2a2a3a" strokeWidth={0.5} />
          <text x={pad.l - 4} y={toY(v) + 3} textAnchor="end" fill="#666" fontSize={9}>{v.toFixed(2)}</text>
        </g>
      ))}
      {/* X axis labels */}
      {[0, Math.floor(data.length / 2), data.length - 1].map((i) => (
        <text key={i} x={toX(i)} y={H - 5} textAnchor="middle" fill="#666" fontSize={9}>
          {data[i].date.slice(5)}
        </text>
      ))}
      {/* Benchmark */}
      <path d={bmPath} fill="none" stroke="#555" strokeWidth={1} strokeDasharray="4 2" />
      {/* Equity */}
      <path d={eqPath} fill="none" stroke="#6366f1" strokeWidth={1.5} />
      {/* Legend */}
      <line x1={pad.l + 10} x2={pad.l + 25} y1={pad.t + 5} y2={pad.t + 5} stroke="#6366f1" strokeWidth={1.5} />
      <text x={pad.l + 28} y={pad.t + 8} fill="#aaa" fontSize={9}>策略</text>
      <line x1={pad.l + 65} x2={pad.l + 80} y1={pad.t + 5} y2={pad.t + 5} stroke="#555" strokeWidth={1} strokeDasharray="4 2" />
      <text x={pad.l + 83} y={pad.t + 8} fill="#aaa" fontSize={9}>基准</text>
    </svg>
  );
}


/* ─────────────────────── Market Train Panel ─────────────────────── */

type TrainTask = {
  task_id: string;
  model_type: string;
  gpu_id?: number;
  max_stocks: number;
  epochs: number;
  status: string;
  progress: number;
  message: string;
  started_at: number;
  ended_at: number | null;
  result: Record<string, unknown> | null;
  codes_used: number;
  total_codes: number;
};

const MODEL_COLORS: Record<string, { bg: string; ring: string; text: string; bar: string }> = {
  lightgbm:    { bg: "bg-green-900/30",  ring: "ring-green-700",  text: "text-green-400",  bar: "bg-green-500" },
  transformer: { bg: "bg-purple-900/30", ring: "ring-purple-700", text: "text-purple-400", bar: "bg-purple-500" },
  lstm:        { bg: "bg-blue-900/30",   ring: "ring-blue-700",   text: "text-blue-400",   bar: "bg-blue-500" },
  cnn_lstm:    { bg: "bg-amber-900/30",  ring: "ring-amber-700",  text: "text-amber-400",  bar: "bg-amber-500" },
  rl_ppo:      { bg: "bg-cyan-900/30",   ring: "ring-cyan-700",   text: "text-cyan-400",   bar: "bg-cyan-500" },
};

const MODEL_LABELS: Record<string, string> = {
  lightgbm: "LightGBM", transformer: "Transformer", lstm: "LSTM",
  cnn_lstm: "CNN-LSTM", rl_ppo: "RL-PPO",
};

function MarketTrainPanel({ modelType, onTrained }: { modelType: ModelType; onTrained: () => void }) {
  const [maxStocks, setMaxStocks] = useState(200);
  const [epochs, setEpochs] = useState(100);
  const [numGpus, setNumGpus] = useState(1);
  const [tasks, setTasks] = useState<TrainTask[]>([]);
  const [launching, setLaunching] = useState(false);

  // Poll progress every 3s when there are active tasks
  useEffect(() => {
    const poll = () => {
      api.aiTrainProgress().then(setTasks).catch(() => {});
    };
    poll();
    const iv = setInterval(poll, 3000);
    return () => clearInterval(iv);
  }, []);

  // Refresh model status when any task completes
  useEffect(() => {
    if (tasks.some((t) => t.status === "completed")) onTrained();
  }, [tasks, onTrained]);

  const activeTasks = tasks.filter((t) => ["pending", "loading", "training"].includes(t.status));
  const doneTasks = tasks.filter((t) => ["completed", "failed", "cancelled"].includes(t.status));

  const launch = (type: string) => {
    setLaunching(true);
    api.aiTrainMarket({ model_type: type, max_stocks: maxStocks, epochs, num_gpus: numGpus })
      .then(() => api.aiTrainProgress().then(setTasks))
      .catch(() => {})
      .finally(() => setLaunching(false));
  };

  const cancel = (taskId: string) => {
    fetch(`/api/strategy/train_progress/${taskId}`, { method: "DELETE" })
      .then(() => api.aiTrainProgress().then(setTasks));
  };

  const clearHistory = () => {
    fetch("/api/strategy/train_progress", { method: "DELETE" })
      .then(() => api.aiTrainProgress().then(setTasks));
  };

  return (
    <div className="bg-ink-900 rounded-xl border border-ink-800 p-4 space-y-3">
      <h3 className="text-xs font-semibold text-ink-300 flex items-center gap-2">
        <i className="fas fa-server text-[10px]" /> Ray 分布式训练
        <span className="text-[10px] text-ink-600 font-normal">6x RTX 3080 Ti · Ray DDP</span>
      </h3>

      {/* Config */}
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-[10px] text-ink-500 mb-0.5 block">样本股数</label>
          <input type="number" className="inp w-full text-xs" value={maxStocks}
                 onChange={(e) => setMaxStocks(Number(e.target.value))} min={50} max={5000} step={50} />
        </div>
        <div>
          <label className="text-[10px] text-ink-500 mb-0.5 block">Epochs</label>
          <input type="number" className="inp w-full text-xs" value={epochs}
                 onChange={(e) => setEpochs(Number(e.target.value))} min={10} max={500} />
        </div>
        <div>
          <label className="text-[10px] text-ink-500 mb-0.5 block">GPU</label>
          <select className="inp w-full text-xs" value={numGpus}
                  onChange={(e) => setNumGpus(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <option key={n} value={n}>{n} GPU{n > 1 ? " DDP" : ""}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Launch buttons */}
      <div className="flex gap-1.5 flex-wrap">
        <button className="btn-gold text-[11px] px-3 py-1.5" onClick={() => launch("all")} disabled={launching}>
          <i className="fas fa-rocket mr-1" /> 全部训练 (Ray)
        </button>
        <button className="btn-outline text-[11px] px-2 py-1.5" onClick={() => launch(modelType === "ensemble" ? "rl_ppo" : modelType)}
                disabled={launching}>
          <i className="fas fa-play mr-1" /> 训练 {MODEL_LABELS[modelType === "ensemble" ? "rl_ppo" : modelType]}
        </button>
      </div>

      {/* Active tasks */}
      {activeTasks.length > 0 && (
        <div className="space-y-2">
          {activeTasks.map((t) => {
            const c = MODEL_COLORS[t.model_type] || MODEL_COLORS.lightgbm;
            return (
              <div key={t.task_id} className={`rounded-lg border ${c.ring} ${c.bg} p-3`}>
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-semibold ${c.text}`}>{MODEL_LABELS[t.model_type]}</span>
                    <span className="text-[10px] text-ink-500">
                      {typeof t.gpu_id === "number" && t.gpu_id > 1 ? `${t.gpu_id} GPU DDP` : t.gpu_id === -1 ? "CPU" : "1 GPU"}
                    </span>
                    <span className="text-[10px] text-ink-600">{t.task_id}</span>
                  </div>
                  <button onClick={() => cancel(t.task_id)} className="text-[10px] text-red-500 hover:text-red-400">
                    <i className="fas fa-stop mr-0.5" /> 取消
                  </button>
                </div>
                {/* Progress bar */}
                <div className="w-full h-1.5 bg-ink-800 rounded-full overflow-hidden mb-1">
                  <div className={`h-full ${c.bar} transition-all duration-500`}
                       style={{ width: `${t.progress}%` }} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-ink-400">{t.message}</span>
                  <span className={`text-[10px] font-mono ${c.text}`}>{t.progress.toFixed(0)}%</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Completed tasks */}
      {doneTasks.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-ink-500">历史记录</span>
            <button onClick={clearHistory} className="text-[10px] text-ink-600 hover:text-ink-400">
              <i className="fas fa-trash mr-0.5" /> 清除
            </button>
          </div>
          {doneTasks.slice(-5).reverse().map((t) => {
            const c = MODEL_COLORS[t.model_type] || MODEL_COLORS.lightgbm;
            const isOk = t.status === "completed";
            const r = t.result as Record<string, unknown> | null;
            return (
              <div key={t.task_id} className="flex items-center gap-2 py-1 px-2 rounded bg-ink-850 text-[10px]">
                <span className={`w-1.5 h-1.5 rounded-full ${isOk ? "bg-green-500" : "bg-red-500"}`} />
                <span className={c.text}>{MODEL_LABELS[t.model_type]}</span>
                <span className="text-ink-500">{t.codes_used} 只</span>
                {isOk && r && (
                  <>
                    {r.accuracy != null && <span className="text-ink-400">准确率 {(Number(r.accuracy) * 100).toFixed(1)}%</span>}
                    {r.win_rate != null && <span className="text-ink-400">胜率 {Number(r.win_rate).toFixed(1)}%</span>}
                    {r.elapsed_sec != null && <span className="text-ink-600">{Number(r.elapsed_sec).toFixed(0)}s</span>}
                  </>
                )}
                {!isOk && <span className="text-red-500">{t.message}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


/* ─────────────────────── AI Scan Panel ─────────────────────── */

type ScanHit = {
  code: string; name: string; model: string; action: string;
  confidence: number; current_price: number; entry_price: number;
  stop_loss: number; target_price: number; risk_reward: number;
  trend: string; reason: string;
};

type ScanTask = {
  task_id: string; scope: string; status: string; progress: number;
  message: string; total: number; scanned: number;
  started_at: number; ended_at: number | null;
  results: ScanHit[];
};

function ScanPanel({
  code, modelType, status, setCode,
}: {
  code: string;
  modelType: ModelType;
  status: AiModelStatus | null;
  setCode: (c: string) => void;
}) {
  const [scope, setScope] = useState<"watchlist" | "industry" | "cached">("watchlist");
  const [scopeCode, setScopeCode] = useState(code);
  const [industryInfo, setIndustryInfo] = useState<{ industry: string; stocks: { code: string; name: string }[] } | null>(null);
  const [useAllModels, setUseAllModels] = useState(true);
  const [threshold, setThreshold] = useState(0.35);
  const [launching, setLaunching] = useState(false);
  const [tasks, setTasks] = useState<ScanTask[]>([]);
  const [sortKey, setSortKey] = useState<keyof ScanHit>(() => (localStorage.getItem("aiscan_sortKey") as keyof ScanHit) || "confidence");
  const [sortAsc, setSortAsc] = useState(() => localStorage.getItem("aiscan_sortAsc") === "true");

  // Poll progress
  useEffect(() => {
    const poll = () => { api.aiScanProgress().then(setTasks).catch(() => {}); };
    poll();
    const iv = setInterval(poll, 2000);
    return () => clearInterval(iv);
  }, []);

  // Fetch industry info when scope changes
  useEffect(() => {
    if (scope === "industry" && scopeCode) {
      api.aiIndustryStocks(scopeCode).then(setIndustryInfo).catch(() => setIndustryInfo(null));
    }
  }, [scope, scopeCode]);

  const trainedModels = status
    ? (Object.entries(status) as [string, { trained: boolean }][])
        .filter(([, v]) => v.trained)
        .map(([k]) => k)
    : [];

  const startScan = () => {
    const modelTypes = useAllModels ? trainedModels : [modelType === "ensemble" ? "lightgbm" : modelType];
    if (!modelTypes.length) return;
    setLaunching(true);
    api.aiScan({
      scope,
      scope_code: scopeCode,
      model_types: modelTypes,
      buy_threshold: threshold,
      sell_threshold: threshold,
    })
      .then(() => api.aiScanProgress().then(setTasks))
      .catch(() => {})
      .finally(() => setLaunching(false));
  };

  const activeTask = tasks.find((t) => ["pending", "loading", "scanning"].includes(t.status));
  const latestDone = tasks.find((t) => t.status === "completed");
  const results = (activeTask?.results ?? latestDone?.results ?? []);
  const toggleSort = (key: keyof ScanHit) => {
    if (sortKey === key) {
      const next = !sortAsc;
      setSortAsc(next);
      localStorage.setItem("aiscan_sortAsc", String(next));
    } else {
      setSortKey(key);
      setSortAsc(false);
      localStorage.setItem("aiscan_sortKey", key);
      localStorage.setItem("aiscan_sortAsc", "false");
    }
  };
  const sorted = [...results].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
    return sortAsc ? cmp : -cmp;
  });

  // Group by action
  const buys = sorted.filter((r) => r.action === "buy");
  const sells = sorted.filter((r) => r.action === "sell");

  return (
    <div className="flex gap-4 p-5">
      {/* Left: config */}
      <div className="w-72 space-y-4 shrink-0">
        <div className="bg-ink-900 rounded-xl border border-ink-800 p-4 space-y-3">
          <h3 className="text-xs font-semibold text-ink-300 flex items-center gap-2">
            <i className="fas fa-satellite-dish text-[10px]" /> 扫描配置
          </h3>

          {/* Scope */}
          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">扫描范围</label>
            <div className="flex gap-1.5">
              {([
                { k: "watchlist" as const, l: "自选", icon: "fa-star" },
                { k: "industry" as const, l: "同行业", icon: "fa-industry" },
                { k: "cached" as const, l: "全部", icon: "fa-database" },
              ]).map((s) => (
                <button key={s.k}
                  className={`flex-1 text-[11px] py-1.5 rounded-lg border transition-colors ${
                    scope === s.k
                      ? "bg-amber-500/20 border-amber-500/50 text-amber-400"
                      : "border-ink-700 text-ink-400 hover:border-ink-600"
                  }`}
                  onClick={() => setScope(s.k)}
                >
                  <i className={`fas ${s.icon} mr-1 text-[9px]`} /> {s.l}
                </button>
              ))}
            </div>
          </div>

          {/* Industry code input */}
          {scope === "industry" && (
            <div>
              <label className="text-[11px] text-ink-500 mb-1 block">参考股票代码</label>
              <input className="inp w-full text-xs" value={scopeCode}
                     onChange={(e) => setScopeCode(e.target.value)}
                     placeholder="600519" />
              {industryInfo && (
                <div className="mt-1.5 text-[10px] text-blue-400">
                  <i className="fas fa-sitemap mr-1" />
                  {industryInfo.industry} ({industryInfo.stocks.length}只)
                </div>
              )}
            </div>
          )}

          {/* Model selection */}
          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">使用模型</label>
            <div className="flex gap-1.5">
              <button
                className={`flex-1 text-[11px] py-1.5 rounded-lg border transition-colors ${
                  useAllModels ? "bg-purple-500/20 border-purple-500/50 text-purple-400"
                               : "border-ink-700 text-ink-400 hover:border-ink-600"
                }`}
                onClick={() => setUseAllModels(true)}
              >
                全部已训练
              </button>
              <button
                className={`flex-1 text-[11px] py-1.5 rounded-lg border transition-colors ${
                  !useAllModels ? "bg-purple-500/20 border-purple-500/50 text-purple-400"
                                : "border-ink-700 text-ink-400 hover:border-ink-600"
                }`}
                onClick={() => setUseAllModels(false)}
              >
                仅当前模型
              </button>
            </div>
            {!useAllModels && (
              <div className="text-[10px] text-ink-500 mt-1">
                当前: {MODEL_LABELS[modelType] ?? modelType}
              </div>
            )}
            {useAllModels && trainedModels.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {trainedModels.map((m) => (
                  <span key={m} className={`text-[10px] px-1.5 py-0.5 rounded ${MODEL_COLORS[m]?.bg ?? ""} ${MODEL_COLORS[m]?.text ?? "text-ink-400"}`}>
                    {MODEL_LABELS[m] ?? m}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Threshold */}
          <div>
            <label className="text-[11px] text-ink-500 mb-1 block">信号阈值</label>
            <input type="number" className="inp w-full text-xs" value={threshold}
                   onChange={(e) => setThreshold(Number(e.target.value))}
                   step={0.05} min={0.1} max={0.9} />
            <div className="text-[10px] text-ink-600 mt-0.5">
              概率 ≥ {(threshold * 100).toFixed(0)}% 视为信号
            </div>
          </div>

          {/* Launch */}
          <button className="btn-gold w-full text-xs py-2.5" onClick={startScan}
                  disabled={launching || !!activeTask || trainedModels.length === 0}>
            {activeTask ? (
              <><i className="fas fa-spinner fa-spin mr-1" /> 扫描中...</>
            ) : trainedModels.length === 0 ? (
              <><i className="fas fa-exclamation-triangle mr-1" /> 请先训练模型</>
            ) : (
              <><i className="fas fa-search mr-1" /> 开始扫描</>
            )}
          </button>

          {activeTask && (
            <button className="btn-outline w-full text-[11px] py-1.5 text-red-400 border-red-800 hover:bg-red-900/20"
                    onClick={() => api.aiScanCancel(activeTask.task_id).then(() => api.aiScanProgress().then(setTasks))}>
              <i className="fas fa-stop mr-1" /> 取消扫描
            </button>
          )}
        </div>

        {/* Scan stats */}
        <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
          <div className="grid grid-cols-2 gap-2 text-center text-xs">
            <div className="bg-ink-850 rounded-lg p-2">
              <div className="text-[10px] text-ink-500">买入信号</div>
              <div className="num text-green-400 text-lg font-bold">{buys.length}</div>
            </div>
            <div className="bg-ink-850 rounded-lg p-2">
              <div className="text-[10px] text-ink-500">卖出信号</div>
              <div className="num text-red-400 text-lg font-bold">{sells.length}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Right: results */}
      <div className="flex-1 space-y-4">
        {/* Progress */}
        {activeTask && (
          <div className="bg-ink-900 rounded-xl border border-ink-800 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-ink-300">{activeTask.message}</span>
              <span className="text-xs num text-amber-400">{activeTask.progress}%</span>
            </div>
            <div className="w-full h-2 bg-ink-800 rounded-full overflow-hidden">
              <div className="h-full bg-amber-500 transition-all duration-500 rounded-full"
                   style={{ width: `${activeTask.progress}%` }} />
            </div>
          </div>
        )}

        {results.length > 0 && (
          <div className="text-[10px] text-ink-600 text-right">
            共 {results.length} 个信号
          </div>
        )}

        {/* Results table */}
        {sorted.length > 0 ? (
          <div className="bg-ink-900 rounded-xl border border-ink-800 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ink-500 border-b border-ink-800 bg-ink-850">
                  {([
                    { key: "name", label: "股票", align: "left", px: "px-3" },
                    { key: "model", label: "模型", align: "center" },
                    { key: "action", label: "信号", align: "center" },
                    { key: "confidence", label: "置信度", align: "right" },
                    { key: "current_price", label: "当前价", align: "right" },
                    { key: "entry_price", label: "入场价", align: "right" },
                    { key: "stop_loss", label: "止损价", align: "right" },
                    { key: "target_price", label: "目标价", align: "right" },
                    { key: "risk_reward", label: "盈亏比", align: "right" },
                    { key: "trend", label: "趋势", align: "center" },
                  ] as { key: keyof ScanHit; label: string; align: string; px?: string }[]).map((col) => (
                    <th key={col.key}
                        className={`text-${col.align} py-2 ${col.px ?? "px-2"} cursor-pointer select-none hover:text-ink-300 transition-colors whitespace-nowrap`}
                        onClick={() => toggleSort(col.key)}>
                      {col.label}
                      {sortKey === col.key && (
                        <i className={`fas fa-caret-${sortAsc ? "up" : "down"} ml-1 text-[9px] text-blue-400`} />
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => {
                  const mc = MODEL_COLORS[r.model] || MODEL_COLORS.lightgbm;
                  return (
                    <tr key={`${r.code}-${r.model}-${i}`}
                        className="border-b border-ink-800/50 hover:bg-ink-850 cursor-pointer transition-colors"
                        onClick={() => setCode(r.code)}>
                      <td className="py-2 px-3">
                        <div className="text-ink-200 font-medium">{r.name}</div>
                        <div className="text-[10px] text-ink-500 num">{r.code}</div>
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${mc.bg} ${mc.text}`}>
                          {MODEL_LABELS[r.model] ?? r.model}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                          r.action === "buy" ? "bg-green-900/40 text-green-400" : "bg-red-900/40 text-red-400"
                        }`}>
                          {r.action === "buy" ? "▲ 买入" : "▼ 卖出"}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <span className={`num font-medium ${r.confidence >= 60 ? "text-amber-400" : "text-ink-300"}`}>
                          {r.confidence.toFixed(1)}%
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right num text-ink-300">{r.current_price.toFixed(2)}</td>
                      <td className="py-2 px-2 text-right num text-blue-400">{r.entry_price.toFixed(2)}</td>
                      <td className="py-2 px-2 text-right num text-red-400">{r.stop_loss.toFixed(2)}</td>
                      <td className="py-2 px-2 text-right num text-green-400">{r.target_price.toFixed(2)}</td>
                      <td className="py-2 px-2 text-right">
                        <span className={`num font-medium ${r.risk_reward >= 2 ? "text-green-400" : r.risk_reward >= 1 ? "text-ink-300" : "text-red-400"}`}>
                          {r.risk_reward.toFixed(2)}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className={`text-[10px] ${r.trend === "up" ? "text-green-500" : r.trend === "down" ? "text-red-500" : "text-ink-500"}`}>
                          {r.trend === "up" ? "↑" : r.trend === "down" ? "↓" : "—"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : !activeTask && (
          <div className="flex flex-col items-center justify-center h-64 text-ink-600">
            <i className="fas fa-satellite-dish text-4xl mb-3 opacity-30" />
            <div className="text-sm">点击"开始扫描"寻找交易机会</div>
            <div className="text-[11px] mt-1">AI模型将逐一分析股票并筛选出买卖信号</div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────── HMM 市场状态 ─────────────────────── */

function RegimePanel() {
  const [code, setCode] = useState("000001");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RegimeFitResult | null>(null);

  const fit = async () => {
    setLoading(true);
    try { setResult(await api.regimeFit({ code })); } finally { setLoading(false); }
  };

  const COLORS = ["text-emerald-400", "text-amber-400", "text-red-400"];
  const BG = ["bg-emerald-900/30", "bg-amber-900/30", "bg-red-900/30"];

  return (
    <div className="p-5 max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-1">
        <i className="fas fa-chart-area mr-2 text-amber-400" />HMM 市场状态识别
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        隐马尔可夫模型将市场分为趋势/震荡/危机三种状态，辅助策略选择和仓位调整。
      </p>
      <div className="flex items-end gap-3 mb-5">
        <div>
          <div className="text-[10px] text-ink-500 mb-1">指数/股票代码</div>
          <input className="inp w-28" value={code} onChange={(e) => setCode(e.target.value)} />
        </div>
        <button className="btn-gold" onClick={fit} disabled={loading}>
          {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" />拟合中...</> : "拟合 HMM"}
        </button>
      </div>
      {result && (
        <div className="bg-ink-900 rounded-lg p-4 ring-soft">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-[11px] text-ink-400">当前状态:</span>
            <span className={"text-lg font-bold " + COLORS[result.current_regime.id]}>{result.current_regime.name}</span>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            {result.regimes.map((r) => (
              <div key={r.id} className={"rounded-lg p-3 ring-soft " + BG[r.id] + (r.id === result.current_regime.id ? " ring-2 ring-gold/30" : "")}>
                <div className={"text-[13px] font-semibold mb-1 " + COLORS[r.id]}>{r.name}</div>
                <div className="text-[10px] text-ink-400 space-y-0.5">
                  <div>日均收益: <span className="num">{r.mean_return.toFixed(3)}%</span></div>
                  <div>波动率: <span className="num">{r.mean_vol.toFixed(3)}%</span></div>
                  <div>占比: <span className="num">{r.pct.toFixed(1)}%</span></div>
                </div>
              </div>
            ))}
          </div>
          <div className="mb-3">
            <div className="tag text-ink-500 mb-1">近30日状态序列</div>
            <div className="flex gap-0.5">
              {result.regime_sequence.map((s, i) => (
                <div key={i} className={"w-2.5 h-4 rounded-sm " + (s === 0 ? "bg-emerald-500" : s === 1 ? "bg-amber-500" : "bg-red-500")}
                  title={`Day ${i + 1}: ${["趋势", "震荡", "危机"][s]}`} />
              ))}
            </div>
          </div>
          <details>
            <summary className="text-[11px] text-ink-400 cursor-pointer hover:text-ink-200">转移矩阵</summary>
            <table className="text-[10px] num mt-2">
              <thead><tr><th className="text-ink-500 font-normal px-2">→</th><th className="text-ink-500 font-normal px-2">趋势</th><th className="text-ink-500 font-normal px-2">震荡</th><th className="text-ink-500 font-normal px-2">危机</th></tr></thead>
              <tbody>{result.transition_matrix.map((row, i) => (
                <tr key={i}><td className={"px-2 " + COLORS[i]}>{["趋势", "震荡", "危机"][i]}</td>
                  {row.map((v, j) => <td key={j} className="px-2 text-ink-200">{(v * 100).toFixed(1)}%</td>)}
                </tr>
              ))}</tbody>
            </table>
          </details>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────── RL 仓位管理 ─────────────────────── */

function RlPositionPanel() {
  const [codes, setCodes] = useState("000001,600036");
  const [steps, setSteps] = useState(50000);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [posCode, setPosCode] = useState("000001");
  const [pos, setPos] = useState<{ allocation: number } | null>(null);

  const train = async () => {
    setLoading(true);
    try { setResult(await api.rlTrain({ codes: codes.split(",").map((s) => s.trim()), total_timesteps: steps })); } finally { setLoading(false); }
  };
  const predict = async () => {
    const r = await api.rlPosition(posCode);
    if (!r.error) setPos({ allocation: r.allocation });
  };
  const ALLOC = ["空仓", "25%", "50%", "75%", "满仓"];

  return (
    <div className="p-5 max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-1">
        <i className="fas fa-robot mr-2 text-emerald-400" />RL 动态仓位 (PPO)
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        强化学习代理学习动态仓位管理，输出 0%/25%/50%/75%/100% 五档仓位建议。
      </p>
      <div className="flex items-end gap-3 mb-4">
        <div><div className="text-[10px] text-ink-500 mb-1">训练股票池</div>
          <input className="inp w-56" value={codes} onChange={(e) => setCodes(e.target.value)} /></div>
        <div><div className="text-[10px] text-ink-500 mb-1">训练步数</div>
          <input className="inp w-28" type="number" value={steps} onChange={(e) => setSteps(Number(e.target.value))} /></div>
        <button className="btn-gold" onClick={train} disabled={loading}>
          {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" />训练中...</> : "训练 RL"}
        </button>
      </div>
      {result && !(result as any).error && (
        <div className="bg-ink-900 rounded-lg p-4 ring-soft mb-5">
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-ink-850 rounded-md p-2 text-center"><div className="num text-[14px] font-semibold text-gold">{String(result.eval_total_reward)}</div><div className="text-[9px] text-ink-500 mt-0.5">累计奖励</div></div>
            <div className="bg-ink-850 rounded-md p-2 text-center"><div className="num text-[14px] font-semibold text-sky2">{String((result.eval_final_equity as number)?.toFixed(4))}</div><div className="text-[9px] text-ink-500 mt-0.5">最终权益</div></div>
            <div className="bg-ink-850 rounded-md p-2 text-center"><div className="num text-[14px] font-semibold text-ink-200">{String(result.eval_trades)}</div><div className="text-[9px] text-ink-500 mt-0.5">交易次数</div></div>
          </div>
        </div>
      )}
      <div className="border-t border-ink-800 pt-4">
        <div className="tag text-ink-500 mb-2">实时仓位建议</div>
        <div className="flex items-end gap-3">
          <div><div className="text-[10px] text-ink-500 mb-1">股票代码</div>
            <input className="inp w-28" value={posCode} onChange={(e) => setPosCode(e.target.value)} /></div>
          <button className="btn-outline" onClick={predict}>查询仓位</button>
          {pos && (
            <div className="flex items-center gap-2">
              <div className="flex gap-0.5">
                {[0, 0.25, 0.5, 0.75, 1.0].map((lvl, i) => (
                  <div key={lvl} className={"w-6 h-6 rounded text-[9px] flex items-center justify-center " + (pos.allocation >= lvl ? "bg-emerald-500 text-white" : "bg-ink-800 text-ink-500")}>{ALLOC[i]}</div>
                ))}
              </div>
              <span className="num text-lg font-bold text-emerald-400">{(pos.allocation * 100).toFixed(0)}%</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────── 形态识别 (DTW+CNN) ─────────────────────── */

function PatternPanel() {
  const [code, setCode] = useState("000001");
  const [dtwResult, setDtwResult] = useState<PatternResult | null>(null);
  const [cnnResult, setCnnResult] = useState<PatternResult | null>(null);
  const [trainResult, setTrainResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  const runDtw = async () => { setLoading("dtw"); try { setDtwResult(await api.patternDtw(code)); } finally { setLoading(null); } };
  const runCnn = async () => { setLoading("cnn"); try { setCnnResult(await api.patternCnn(code)); } finally { setLoading(null); } };
  const trainCnn = async () => { setLoading("train"); try { setTrainResult(await api.patternCnnTrain({})); } finally { setLoading(null); } };

  return (
    <div className="p-5 max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-1">
        <i className="fas fa-shapes mr-2 text-purple-400" />形态识别 (DTW + CNN)
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        DTW模板匹配可直接使用，CNN需要先训练（基于合成数据，约30秒）。
      </p>
      <div className="flex items-end gap-3 mb-5">
        <div><div className="text-[10px] text-ink-500 mb-1">股票代码</div>
          <input className="inp w-28" value={code} onChange={(e) => setCode(e.target.value)} /></div>
        <button className="btn-gold" onClick={runDtw} disabled={loading === "dtw"}>
          {loading === "dtw" ? <i className="fas fa-circle-notch fa-spin" /> : <i className="fas fa-ruler mr-1" />} DTW 匹配
        </button>
        <button className="btn-outline" onClick={runCnn} disabled={loading === "cnn"}>
          {loading === "cnn" ? <i className="fas fa-circle-notch fa-spin" /> : <i className="fas fa-network-wired mr-1" />} CNN 预测
        </button>
        <button className="btn-outline" onClick={trainCnn} disabled={loading === "train"}>
          {loading === "train" ? <><i className="fas fa-circle-notch fa-spin mr-1" />训练中</> : "训练 CNN"}
        </button>
      </div>
      {trainResult && !(trainResult as any).error && (
        <div className="bg-ink-900 rounded-lg p-3 ring-soft mb-4 text-[11px]">
          <span className="text-ink-400">CNN训练完成 · </span>
          <span className="text-gold num">准确率 {((trainResult.accuracy as number) * 100).toFixed(1)}%</span>
          <span className="text-ink-500"> · 样本 {trainResult.samples as number}</span>
        </div>
      )}
      <div className="grid grid-cols-2 gap-4">
        {dtwResult && (
          <div className="bg-ink-900 rounded-lg p-4 ring-soft">
            <div className="tag text-ink-500 mb-2">DTW 模板匹配</div>
            {dtwResult.patterns.map((p) => (
              <div key={p.pattern_id} className="flex items-center justify-between py-1.5 border-b border-ink-850 last:border-0">
                <span className="text-[12px] text-ink-200">{p.pattern_name}</span>
                <div className="flex items-center gap-2">
                  <div className="w-20 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bg-purple-400" style={{ width: Math.max(0, p.similarity ?? 0) + "%" }} />
                  </div>
                  <span className="num text-[11px] text-purple-400 w-8 text-right">{(p.similarity ?? 0).toFixed(0)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {cnnResult && (
          <div className="bg-ink-900 rounded-lg p-4 ring-soft">
            <div className="tag text-ink-500 mb-2">CNN 深度学习</div>
            {cnnResult.patterns.map((p) => (
              <div key={p.pattern_id} className="flex items-center justify-between py-1.5 border-b border-ink-850 last:border-0">
                <span className="text-[12px] text-ink-200">{p.pattern_name}</span>
                <div className="flex items-center gap-2">
                  <div className="w-20 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bg-sky-400" style={{ width: Math.max(0, p.probability ?? 0) + "%" }} />
                  </div>
                  <span className="num text-[11px] text-sky-400 w-8 text-right">{(p.probability ?? 0).toFixed(0)}%</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
