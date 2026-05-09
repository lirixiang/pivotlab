import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { MlModelKey, MlTrainStatusResp } from "../types";

const MODEL_INFO: Record<MlModelKey, { label: string; desc: string; key_metric: string }> = {
  lgbm: {
    label: "LightGBM 排序器",
    desc: "学习「同一天哪只票未来 10 天涨得最多」, 横截面 LambdaRank, 训练 ~1 分钟",
    key_metric: "val_rank_ic",
  },
  seq: {
    label: "TCN 时序模型",
    desc: "60 日 OHLCV 卷积网络, 学习走势模式, 训练 ~3 分钟 (CPU) / 30 秒 (GPU)",
    key_metric: "final_val_ic",
  },
  rl: {
    label: "PPO 仓位 RL",
    desc: "学习「何时加仓/减仓/止盈」的强化学习智能体, 训练 ~2 分钟",
    key_metric: "eval_avg_reward",
  },
};

export function TrainPanel() {
  const [status, setStatus] = useState<MlTrainStatusResp | null>(null);
  const [busy, setBusy] = useState<MlModelKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setStatus(await api.recommendTrainStatus());
    } catch (e) {
      // ignore — endpoint may briefly 5xx during restart
    }
  }, []);

  useEffect(() => {
    reload();
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [reload]);

  const trigger = async (model: MlModelKey) => {
    setError(null);
    setBusy(model);
    try {
      await api.recommendTrain(model);
      await reload();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  };

  const cur = status?.current;
  const reg = status?.registry || {};

  // Map ML registry name → display key
  const regKey = (m: MlModelKey) =>
    m === "lgbm" ? "lgbm_ranker" : m === "seq" ? "seq_tcn" : "rl_ppo";

  return (
    <div className="border-b border-ink-800 bg-ink-925/40 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-medium text-white">AI 模型训练</div>
        <div className="text-xs text-ink-500">
          {cur?.running ? (
            <span className="text-gold">
              训练中 · {cur.model} · {cur.phase} {cur.pct ?? 0}%
              {cur.epoch && cur.epochs && ` · epoch ${cur.epoch}/${cur.epochs}`}
              {cur.val_ic !== undefined && ` · val_ic=${cur.val_ic.toFixed(3)}`}
            </span>
          ) : (
            <span>空闲</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {(Object.keys(MODEL_INFO) as MlModelKey[]).map((m) => {
          const info = MODEL_INFO[m];
          const meta = reg[regKey(m)];
          const trained = !!meta;
          const metricVal = meta?.[info.key_metric] as number | undefined;
          const isCurrentlyTraining = cur?.running && cur.model === m;
          return (
            <div
              key={m}
              className="border border-ink-800 rounded-md p-2.5 bg-ink-900/40"
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-white">{info.label}</div>
                  <div className="text-[11px] text-ink-500 mt-0.5 leading-tight">
                    {info.desc}
                  </div>
                </div>
                <button
                  disabled={!!cur?.running || busy === m}
                  onClick={() => trigger(m)}
                  className="ml-2 shrink-0 px-2.5 py-1 rounded text-xs bg-gold/90 text-ink-950 font-medium hover:bg-gold disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {isCurrentlyTraining ? `${cur.pct ?? 0}%` : trained ? "重训" : "训练"}
                </button>
              </div>
              <div className="mt-2 flex items-center gap-3 text-[11px]">
                <span
                  className={
                    "px-1.5 py-0.5 rounded " +
                    (trained
                      ? "bg-emerald-500/10 text-emerald-400"
                      : "bg-ink-800 text-ink-500")
                  }
                >
                  {trained ? "已训练" : "未训练"}
                </span>
                {trained && metricVal !== undefined && (
                  <span className="text-ink-400">
                    {info.key_metric}=
                    <span className="text-white font-mono">
                      {typeof metricVal === "number" ? metricVal.toFixed(4) : metricVal}
                    </span>
                  </span>
                )}
                {trained && meta?.saved_at && (
                  <span className="text-ink-500">
                    {new Date(meta.saved_at as string).toLocaleString("zh-CN", {
                      month: "2-digit", day: "2-digit",
                      hour: "2-digit", minute: "2-digit",
                    })}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {error && (
        <div className="mt-2 text-xs text-rose-400">训练失败: {error}</div>
      )}
      <div className="mt-2 text-[11px] text-ink-500">
        训练后,在「AI 集成」标签页里使用结果。三个模型都未训练时, AI 集成会回退到规则评分。
      </div>
    </div>
  );
}
