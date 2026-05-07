import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { AlgoModule, OptimizeResult, MlTrainResult, RegimeFitResult, PatternResult } from "../types";

type Tab = "overview" | "optimize" | "ml" | "rl" | "regime" | "pattern";

export function AlgoPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [modules, setModules] = useState<AlgoModule[]>([]);

  useEffect(() => {
    api.algoStatus().then((r) => setModules(r.modules));
  }, []);

  const TABS: { key: Tab; label: string; icon: string }[] = [
    { key: "overview", label: "总览", icon: "fa-home" },
    { key: "optimize", label: "P0 自动调参", icon: "fa-sliders-h" },
    { key: "ml", label: "P1 ML打分", icon: "fa-brain" },
    { key: "rl", label: "P2 RL仓位", icon: "fa-robot" },
    { key: "regime", label: "P3 市场状态", icon: "fa-chart-area" },
    { key: "pattern", label: "P4 形态识别", icon: "fa-shapes" },
  ];

  return (
    <div className="flex-1 flex flex-col bg-ink-950 overflow-hidden">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-ink-800 grad-head overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] whitespace-nowrap transition " +
              (tab === t.key ? "bg-ink-800 text-white ring-soft" : "text-ink-400 hover:text-white hover:bg-ink-850")
            }
          >
            <i className={"fas " + t.icon + " text-[10px]"} />
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto scrollbar p-5">
        {tab === "overview" && <Overview modules={modules} onGo={setTab} />}
        {tab === "optimize" && <OptimizePanel />}
        {tab === "ml" && <MlPanel />}
        {tab === "rl" && <RlPanel />}
        {tab === "regime" && <RegimePanel />}
        {tab === "pattern" && <PatternPanel />}
      </div>
    </div>
  );
}

/* ── Overview ── */
function Overview({ modules, onGo }: { modules: AlgoModule[]; onGo: (t: Tab) => void }) {
  const tabMap: Record<string, Tab> = { P0: "optimize", P1: "ml", P2: "rl", P3: "regime", P4: "pattern" };
  return (
    <div className="max-w-3xl">
      <h2 className="text-lg font-semibold text-white mb-1">算法策略引擎</h2>
      <p className="text-[12px] text-ink-400 mb-5">5 个算法模块，从参数优化到深度学习形态识别</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {modules.map((m) => (
          <div
            key={m.id}
            className="bg-ink-900 rounded-lg p-4 ring-soft cursor-pointer hover:bg-ink-850 transition"
            onClick={() => onGo(tabMap[m.id] ?? "overview")}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-[13px] font-semibold text-white">{m.id} · {m.name}</span>
              <StatusBadge status={m.status} />
            </div>
            <p className="text-[11px] text-ink-400 leading-relaxed">{m.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    ready: { bg: "bg-emerald-900/40", text: "text-emerald-400", label: "可用" },
    trained: { bg: "bg-sky-900/40", text: "text-sky-400", label: "已训练" },
    fitted: { bg: "bg-sky-900/40", text: "text-sky-400", label: "已拟合" },
    untrained: { bg: "bg-ink-800", text: "text-ink-400", label: "未训练" },
    unfitted: { bg: "bg-ink-800", text: "text-ink-400", label: "未拟合" },
    dtw_only: { bg: "bg-amber-900/40", text: "text-amber-400", label: "DTW可用" },
  };
  const s = map[status] ?? map.untrained;
  return (
    <span className={"text-[10px] px-2 py-0.5 rounded-full " + s.bg + " " + s.text}>
      {s.label}
    </span>
  );
}

/* ── P0: Optimize ── */
function OptimizePanel() {
  const [code, setCode] = useState("000001");
  const [strategy, setStrategy] = useState("breakout_pullback");
  const [target, setTarget] = useState("sharpe");
  const [trials, setTrials] = useState(60);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<OptimizeResult | null>(null);

  const run = async () => {
    setLoading(true);
    try {
      const r = await api.optimize({ code, strategy, target, n_trials: trials });
      setResult(r);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-3">
        <i className="fas fa-sliders-h mr-2 text-gold" />P0 · Optuna 参数自优化
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        贝叶斯优化搜索最佳回测参数组合，无需训练，直接搜索。
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Field label="股票代码">
          <input className="inp" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <Field label="策略">
          <select className="inp" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="breakout_pullback">突破回踩</option>
            <option value="bottom_stabilize">下跌企稳</option>
          </select>
        </Field>
        <Field label="优化目标">
          <select className="inp" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="sharpe">Sharpe 比率</option>
            <option value="return">总收益率</option>
            <option value="calmar">Calmar 比率</option>
          </select>
        </Field>
        <Field label="试验次数">
          <input className="inp" type="number" min={20} max={200} value={trials}
            onChange={(e) => setTrials(Number(e.target.value))} />
        </Field>
      </div>
      <button className="btn-gold mb-5" onClick={run} disabled={loading}>
        {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" />搜索中...</> : "开始优化"}
      </button>
      {result && <OptimizeResultView r={result} />}
    </div>
  );
}

function OptimizeResultView({ r }: { r: OptimizeResult }) {
  const improved = r.best_value > r.default_value;
  const delta = r.best_value - r.default_value;
  return (
    <div className="bg-ink-900 rounded-lg p-4 ring-soft">
      <div className="flex items-center gap-3 mb-4">
        <span className={"text-xl font-bold " + (improved ? "text-emerald-400" : "text-cn-dn")}>
          {improved ? "↑" : "↓"} {Math.abs(delta).toFixed(2)}
        </span>
        <div className="text-[11px] text-ink-400">
          最优 {r.best_value.toFixed(2)} vs 默认 {r.default_value.toFixed(2)} · {r.trials_count} 次试验
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="tag text-ink-500 mb-2">最优参数统计</div>
          <StatsGrid stats={r.best_stats} />
        </div>
        <div>
          <div className="tag text-ink-500 mb-2">默认参数统计</div>
          <StatsGrid stats={r.default_stats} />
        </div>
      </div>
      <details className="mt-4">
        <summary className="text-[11px] text-ink-400 cursor-pointer hover:text-ink-200">最优参数详情</summary>
        <pre className="text-[10px] text-ink-300 mt-2 bg-ink-850 rounded p-3 overflow-x-auto">
          {JSON.stringify(r.best_params, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function StatsGrid({ stats }: { stats: Record<string, number> }) {
  const items = [
    { k: "total_return", l: "总收益", fmt: (v: number) => v.toFixed(2) + "%", color: (v: number) => v >= 0 ? "text-cn-up" : "text-cn-dn" },
    { k: "sharpe", l: "Sharpe", fmt: (v: number) => v.toFixed(2), color: (v: number) => v >= 1 ? "text-gold" : "text-ink-200" },
    { k: "win_rate", l: "胜率", fmt: (v: number) => (v * 100).toFixed(1) + "%", color: (v: number) => v >= 0.5 ? "text-cn-up" : "text-ink-200" },
    { k: "max_drawdown", l: "最大回撤", fmt: (v: number) => v.toFixed(2) + "%", color: () => "text-cn-dn" },
    { k: "total_trades", l: "交易数", fmt: (v: number) => String(v), color: () => "text-ink-200" },
    { k: "profit_factor", l: "盈亏比", fmt: (v: number) => v.toFixed(2), color: (v: number) => v >= 1.5 ? "text-gold" : "text-ink-200" },
  ];
  return (
    <div className="grid grid-cols-3 gap-2">
      {items.map((it) => {
        const v = stats[it.k] ?? 0;
        return (
          <div key={it.k} className="bg-ink-850 rounded-md p-2 text-center">
            <div className={"num text-[13px] font-semibold " + it.color(v)}>{it.fmt(v)}</div>
            <div className="text-[9px] text-ink-500 mt-0.5">{it.l}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ── P1: ML ── */
function MlPanel() {
  const [codes, setCodes] = useState("000001,600036,000858");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<MlTrainResult | null>(null);
  const [scoreCode, setScoreCode] = useState("000001");
  const [score, setScore] = useState<number | null>(null);

  const train = async () => {
    setLoading(true);
    try {
      const r = await api.mlTrain({ codes: codes.split(",").map((s) => s.trim()) });
      setResult(r);
    } finally {
      setLoading(false);
    }
  };

  const getScore = async () => {
    const r = await api.mlScore(scoreCode);
    setScore(r.ml_score ?? null);
  };

  return (
    <div className="max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-3">
        <i className="fas fa-brain mr-2 text-sky2" />P1 · LightGBM ML 信号打分
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        用历史K线数据自动生成标签（未来N日涨跌），训练梯度提升模型。训练仅需几秒。
      </p>
      <div className="flex items-end gap-3 mb-4">
        <Field label="训练股票池 (逗号分隔)">
          <input className="inp w-72" value={codes} onChange={(e) => setCodes(e.target.value)} />
        </Field>
        <button className="btn-gold" onClick={train} disabled={loading}>
          {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" />训练中...</> : "训练模型"}
        </button>
      </div>
      {result && !result.error && (
        <div className="bg-ink-900 rounded-lg p-4 ring-soft mb-5">
          <div className="grid grid-cols-3 gap-3 mb-3">
            <Metric label="AUC" value={(result.auc ?? 0).toFixed(4)} color="text-gold" />
            <Metric label="准确率" value={((result.accuracy ?? 0) * 100).toFixed(1) + "%"} color="text-sky2" />
            <Metric label="样本数" value={String(result.samples ?? 0)} color="text-ink-200" />
          </div>
          {result.feature_importance && (
            <div>
              <div className="tag text-ink-500 mb-2">特征重要性</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(result.feature_importance).slice(0, 8).map(([k, v]) => (
                  <span key={k} className="chip text-[10px]">{k}: <span className="num text-gold ml-0.5">{v}</span></span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {result?.error && <div className="text-cn-dn text-[12px] mb-4">{result.error}</div>}

      <div className="border-t border-ink-800 pt-4">
        <div className="tag text-ink-500 mb-2">实时预测</div>
        <div className="flex items-end gap-3">
          <Field label="股票代码">
            <input className="inp w-28" value={scoreCode} onChange={(e) => setScoreCode(e.target.value)} />
          </Field>
          <button className="btn-outline" onClick={getScore}>查询 ML 分数</button>
          {score !== null && (
            <span className={"num text-lg font-bold " + (score >= 60 ? "text-gold" : "text-ink-300")}>
              {score}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── P2: RL ── */
function RlPanel() {
  const [codes, setCodes] = useState("000001,600036");
  const [steps, setSteps] = useState(50000);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [posCode, setPosCode] = useState("000001");
  const [pos, setPos] = useState<{ allocation: number } | null>(null);

  const train = async () => {
    setLoading(true);
    try {
      const r = await api.rlTrain({ codes: codes.split(",").map((s) => s.trim()), total_timesteps: steps });
      setResult(r);
    } finally {
      setLoading(false);
    }
  };

  const predict = async () => {
    const r = await api.rlPosition(posCode);
    if (!r.error) setPos({ allocation: r.allocation });
  };

  const ALLOC_LABELS = ["空仓", "25%", "50%", "75%", "满仓"];

  return (
    <div className="max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-3">
        <i className="fas fa-robot mr-2 text-emerald-400" />P2 · RL 动态仓位 (PPO)
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        强化学习代理（PPO算法）学习动态仓位管理策略。训练需要数分钟。
      </p>
      <div className="flex items-end gap-3 mb-4">
        <Field label="训练股票池">
          <input className="inp w-56" value={codes} onChange={(e) => setCodes(e.target.value)} />
        </Field>
        <Field label="训练步数">
          <input className="inp w-28" type="number" value={steps} onChange={(e) => setSteps(Number(e.target.value))} />
        </Field>
        <button className="btn-gold" onClick={train} disabled={loading}>
          {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" />训练中...</> : "训练 RL"}
        </button>
      </div>
      {result && !result.error && (
        <div className="bg-ink-900 rounded-lg p-4 ring-soft mb-5">
          <div className="grid grid-cols-3 gap-3">
            <Metric label="累计奖励" value={String(result.eval_total_reward)} color="text-gold" />
            <Metric label="最终权益" value={String((result.eval_final_equity as number)?.toFixed(4))} color="text-sky2" />
            <Metric label="交易次数" value={String(result.eval_trades)} color="text-ink-200" />
          </div>
        </div>
      )}

      <div className="border-t border-ink-800 pt-4">
        <div className="tag text-ink-500 mb-2">实时仓位建议</div>
        <div className="flex items-end gap-3">
          <Field label="股票代码">
            <input className="inp w-28" value={posCode} onChange={(e) => setPosCode(e.target.value)} />
          </Field>
          <button className="btn-outline" onClick={predict}>查询仓位</button>
          {pos && (
            <div className="flex items-center gap-2">
              <div className="flex gap-0.5">
                {[0, 0.25, 0.5, 0.75, 1.0].map((lvl, i) => (
                  <div
                    key={lvl}
                    className={"w-6 h-6 rounded text-[9px] flex items-center justify-center " +
                      (pos.allocation >= lvl ? "bg-emerald-500 text-white" : "bg-ink-800 text-ink-500")}
                    title={ALLOC_LABELS[i]}
                  >{ALLOC_LABELS[i]}</div>
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

/* ── P3: Regime ── */
function RegimePanel() {
  const [code, setCode] = useState("000001");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RegimeFitResult | null>(null);

  const fit = async () => {
    setLoading(true);
    try {
      const r = await api.regimeFit({ code });
      setResult(r);
    } finally {
      setLoading(false);
    }
  };

  const REGIME_COLORS = ["text-emerald-400", "text-amber-400", "text-red-400"];
  const REGIME_BG = ["bg-emerald-900/30", "bg-amber-900/30", "bg-red-900/30"];

  return (
    <div className="max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-3">
        <i className="fas fa-chart-area mr-2 text-amber-400" />P3 · HMM 市场状态识别
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        隐马尔可夫模型（HMM）将市场分为趋势/震荡/危机三种状态，辅助策略选择。
      </p>
      <div className="flex items-end gap-3 mb-4">
        <Field label="指数/股票代码">
          <input className="inp w-28" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <button className="btn-gold" onClick={fit} disabled={loading}>
          {loading ? <><i className="fas fa-circle-notch fa-spin mr-1" />拟合中...</> : "拟合 HMM"}
        </button>
      </div>
      {result && (
        <div className="bg-ink-900 rounded-lg p-4 ring-soft">
          {/* Current regime */}
          <div className="flex items-center gap-2 mb-4">
            <span className="text-[11px] text-ink-400">当前状态:</span>
            <span className={"text-lg font-bold " + REGIME_COLORS[result.current_regime.id]}>
              {result.current_regime.name}
            </span>
          </div>

          {/* Regime cards */}
          <div className="grid grid-cols-3 gap-3 mb-4">
            {result.regimes.map((r) => (
              <div
                key={r.id}
                className={"rounded-lg p-3 ring-soft " + REGIME_BG[r.id] +
                  (r.id === result.current_regime.id ? " ring-2 ring-gold/30" : "")}
              >
                <div className={"text-[13px] font-semibold mb-1 " + REGIME_COLORS[r.id]}>{r.name}</div>
                <div className="text-[10px] text-ink-400 space-y-0.5">
                  <div>日均收益: <span className="num">{r.mean_return.toFixed(3)}%</span></div>
                  <div>波动率: <span className="num">{r.mean_vol.toFixed(3)}%</span></div>
                  <div>占比: <span className="num">{r.pct.toFixed(1)}%</span></div>
                </div>
              </div>
            ))}
          </div>

          {/* Regime sequence (last 30 bars) */}
          <div className="mb-3">
            <div className="tag text-ink-500 mb-1">近30日状态序列</div>
            <div className="flex gap-0.5">
              {result.regime_sequence.map((s, i) => (
                <div
                  key={i}
                  className={"w-2.5 h-4 rounded-sm " +
                    (s === 0 ? "bg-emerald-500" : s === 1 ? "bg-amber-500" : "bg-red-500")}
                  title={`Day ${i + 1}: ${["趋势", "震荡", "危机"][s]}`}
                />
              ))}
            </div>
          </div>

          {/* Transition matrix */}
          <details>
            <summary className="text-[11px] text-ink-400 cursor-pointer hover:text-ink-200">转移矩阵</summary>
            <table className="text-[10px] num mt-2">
              <thead>
                <tr>
                  <th className="text-ink-500 font-normal px-2">→</th>
                  <th className="text-ink-500 font-normal px-2">趋势</th>
                  <th className="text-ink-500 font-normal px-2">震荡</th>
                  <th className="text-ink-500 font-normal px-2">危机</th>
                </tr>
              </thead>
              <tbody>
                {result.transition_matrix.map((row, i) => (
                  <tr key={i}>
                    <td className={"px-2 " + REGIME_COLORS[i]}>{["趋势", "震荡", "危机"][i]}</td>
                    {row.map((v, j) => (
                      <td key={j} className="px-2 text-ink-200">{(v * 100).toFixed(1)}%</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      )}
    </div>
  );
}

/* ── P4: Pattern ── */
function PatternPanel() {
  const [code, setCode] = useState("000001");
  const [dtwResult, setDtwResult] = useState<PatternResult | null>(null);
  const [cnnResult, setCnnResult] = useState<PatternResult | null>(null);
  const [trainResult, setTrainResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  const runDtw = async () => {
    setLoading("dtw");
    try {
      const r = await api.patternDtw(code);
      setDtwResult(r);
    } finally {
      setLoading(null);
    }
  };

  const runCnn = async () => {
    setLoading("cnn");
    try {
      const r = await api.patternCnn(code);
      setCnnResult(r);
    } finally {
      setLoading(null);
    }
  };

  const trainCnn = async () => {
    setLoading("train");
    try {
      const r = await api.patternCnnTrain({});
      setTrainResult(r);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="max-w-3xl">
      <h3 className="text-[14px] font-semibold text-white mb-3">
        <i className="fas fa-shapes mr-2 text-purple-400" />P4 · 形态识别 (DTW + CNN)
      </h3>
      <p className="text-[11px] text-ink-400 mb-4">
        DTW模板匹配可直接使用。CNN需要先训练（基于合成数据，约30秒）。
      </p>

      <div className="flex items-end gap-3 mb-5">
        <Field label="股票代码">
          <input className="inp w-28" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <button className="btn-gold" onClick={runDtw} disabled={loading === "dtw"}>
          {loading === "dtw" ? <><i className="fas fa-circle-notch fa-spin mr-1" /></> : <><i className="fas fa-ruler mr-1" /></>}
          DTW 匹配
        </button>
        <button className="btn-outline" onClick={runCnn} disabled={loading === "cnn"}>
          {loading === "cnn" ? <><i className="fas fa-circle-notch fa-spin mr-1" /></> : <><i className="fas fa-network-wired mr-1" /></>}
          CNN 预测
        </button>
        <button className="btn-outline" onClick={trainCnn} disabled={loading === "train"}>
          {loading === "train" ? <><i className="fas fa-circle-notch fa-spin mr-1" />训练中</> : "训练 CNN"}
        </button>
      </div>

      {trainResult && !trainResult.error && (
        <div className="bg-ink-900 rounded-lg p-3 ring-soft mb-4 text-[11px]">
          <span className="text-ink-400">CNN训练完成 · </span>
          <span className="text-gold num">准确率 {((trainResult.accuracy as number) * 100).toFixed(1)}%</span>
          <span className="text-ink-500"> · 样本 {trainResult.samples as number}</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* DTW results */}
        {dtwResult && (
          <div className="bg-ink-900 rounded-lg p-4 ring-soft">
            <div className="tag text-ink-500 mb-2">DTW 模板匹配</div>
            {dtwResult.patterns.map((p) => (
              <div key={p.pattern_id} className="flex items-center justify-between py-1.5 border-b border-ink-850 last:border-0">
                <span className="text-[12px] text-ink-200">{p.pattern_name}</span>
                <div className="flex items-center gap-2">
                  <div className="w-20 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-purple-400"
                      style={{ width: Math.max(0, p.similarity ?? 0) + "%" }}
                    />
                  </div>
                  <span className="num text-[11px] text-purple-400 w-8 text-right">{(p.similarity ?? 0).toFixed(0)}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* CNN results */}
        {cnnResult && (
          <div className="bg-ink-900 rounded-lg p-4 ring-soft">
            <div className="tag text-ink-500 mb-2">CNN 深度学习</div>
            {cnnResult.patterns.map((p) => (
              <div key={p.pattern_id} className="flex items-center justify-between py-1.5 border-b border-ink-850 last:border-0">
                <span className="text-[12px] text-ink-200">{p.pattern_name}</span>
                <div className="flex items-center gap-2">
                  <div className="w-20 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-sky-400"
                      style={{ width: Math.max(0, p.probability ?? 0) + "%" }}
                    />
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

/* ── Shared components ── */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] text-ink-500 mb-1">{label}</div>
      {children}
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-ink-850 rounded-md p-2 text-center">
      <div className={"num text-[14px] font-semibold " + color}>{value}</div>
      <div className="text-[9px] text-ink-500 mt-0.5">{label}</div>
    </div>
  );
}
