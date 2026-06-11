#!/usr/bin/env python3
"""模型评估脚本 — 在测试集上跑推理，输出分类报告 + 回测对比。

Usage (inside container):
    # 评估单个模型
    python eval_cli.py --model transformer --stocks 100

    # 评估所有已训练模型
    python eval_cli.py --model all --stocks 200

    # 对比所有模型 + 跑回测
    python eval_cli.py --model all --stocks 200 --backtest

    # 指定 GPU
    python eval_cli.py --model transformer --stocks 100 --gpu 0

Usage (from host):
    docker exec pivotlab python /app/backend/eval_cli.py --model all --stocks 100
    docker exec pivotlab python /app/backend/eval_cli.py --model transformer --stocks 200 --backtest
"""
import argparse
import os
import sys
import time
import logging
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s :: %(message)s")
logger = logging.getLogger("eval_cli")

_MODEL_DIR = Path(__file__).resolve().parent / "models"
PYTORCH_MODELS = ["transformer", "lstm", "cnn_lstm"]
ALL_MODELS = ["lightgbm", "transformer", "lstm", "cnn_lstm", "rl_ppo"]


def load_data(max_stocks: int, min_days: int):
    """Load candle data from PostgreSQL."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.services.data_provider import _cache_read

    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    if not sync_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    engine = create_engine(sync_url)
    # Use fixed seed for reproducibility (not random)
    with Session(engine) as session:
        rows = session.execute(text(
            "SELECT code FROM daily_candles "
            "GROUP BY code HAVING COUNT(*) >= :min_days "
            "ORDER BY code LIMIT :limit"
        ), {"min_days": min_days, "limit": max_stocks}).fetchall()
        codes = [r[0] for r in rows]
    engine.dispose()

    if not codes:
        logger.error("No stocks found with >= %d days", min_days)
        sys.exit(1)

    candle_lists = [
        c for code in codes
        if (c := _cache_read(code, limit=730)) and len(c) >= min_days
    ]
    logger.info("Loaded %d stocks for evaluation", len(candle_lists))
    return candle_lists


def eval_pytorch_model(model_type: str, candle_lists: list):
    """Evaluate a PyTorch model (transformer/lstm/cnn_lstm) on test set."""
    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from sklearn.metrics import classification_report, confusion_matrix
    from app.services.ai_strategy import (
        build_sequence_dataset, NUM_FEATURES, _MODEL_DIR,
        _build_transformer, _build_lstm, _build_cnn_lstm,
    )
    import pickle

    model_keys = {"transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm"}
    model_key = model_keys[model_type]
    model_path = _MODEL_DIR / f"{model_key}.pt"
    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"

    if not model_path.exists():
        return {"error": f"模型文件不存在: {model_path}"}
    if not norm_path.exists():
        return {"error": f"归一化文件不存在: {norm_path}"}

    # Build dataset
    all_X, all_y = [], []
    for candles in candle_lists:
        if len(candles) < 120:
            continue
        X, y = build_sequence_dataset(candles)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return {"error": "无法构建数据集"}

    X = np.concatenate(all_X)
    y = np.concatenate(all_y)

    # Normalize using saved normalization params
    with open(norm_path, "rb") as f:
        norm = pickle.load(f)
    X = (X - norm["mean"]) / norm["std"]

    # Split same as training: 80/20
    split = int(len(X) * 0.8)
    X_test = X[split:]
    y_test = y[split:]

    if len(X_test) == 0:
        return {"error": "测试集为空"}

    # Load model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    builders = {"transformer": _build_transformer, "lstm": _build_lstm, "cnn_lstm": _build_cnn_lstm}
    model = builders[model_type]()().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.eval()

    # Inference
    test_ds = TensorDataset(torch.from_numpy(X_test).float(), torch.from_numpy(y_test).long())
    test_dl = DataLoader(test_ds, batch_size=128)

    all_preds = []
    all_probs = []
    all_true = []

    t0 = time.time()
    with torch.no_grad():
        for xb, yb in test_dl:
            xb = xb.to(device)
            logits = model(xb)
            probs = torch.softmax(logits, dim=1).cpu().numpy()
            preds = logits.argmax(dim=1).cpu().numpy()
            all_preds.extend(preds.tolist())
            all_probs.extend(probs.tolist())
            all_true.extend(yb.numpy().tolist())
    infer_time = time.time() - t0

    # Metrics
    target_names = ["hold", "buy", "sell"]
    report = classification_report(all_true, all_preds, target_names=target_names,
                                   output_dict=True, zero_division=0)
    cm = confusion_matrix(all_true, all_preds)

    # Confidence analysis
    probs_array = np.array(all_probs)
    max_probs = probs_array.max(axis=1)
    correct_mask = np.array(all_preds) == np.array(all_true)

    return {
        "model": model_type,
        "model_file": str(model_path),
        "device": str(device),
        "test_samples": len(y_test),
        "train_samples": split,
        "total_samples": len(y),
        "accuracy": round(report["accuracy"], 4),
        "report": report,
        "confusion_matrix": cm.tolist(),
        "class_distribution": {
            "hold": int((y_test == 0).sum()),
            "buy": int((y_test == 1).sum()),
            "sell": int((y_test == 2).sum()),
        },
        "inference_time_ms": round(infer_time * 1000, 1),
        "samples_per_sec": round(len(y_test) / infer_time),
        "avg_confidence": round(float(max_probs.mean()) * 100, 1),
        "confidence_when_correct": round(float(max_probs[correct_mask].mean()) * 100, 1) if correct_mask.any() else 0,
        "confidence_when_wrong": round(float(max_probs[~correct_mask].mean()) * 100, 1) if (~correct_mask).any() else 0,
    }


def eval_lightgbm(candle_lists: list):
    """Evaluate LightGBM model on test set."""
    from sklearn.metrics import classification_report, confusion_matrix
    from app.services.ai_strategy import (
        extract_features, FEATURE_NAMES, _MODEL_DIR,
    )
    import pickle

    model_path = _MODEL_DIR / "ai_lgb.pkl"
    if not model_path.exists():
        return {"error": f"模型文件不存在: {model_path}"}

    with open(model_path, "rb") as f:
        lgb_model = pickle.load(f)

    # Build flat feature dataset
    all_X, all_y = [], []
    for candles in candle_lists:
        n = len(candles)
        if n < 120:
            continue
        # Use last 20% as test
        test_start = int(n * 0.8)
        for i in range(max(test_start, 60), n - 5):
            feat = extract_features(candles, i)
            if feat is None:
                continue
            row = [feat[k] for k in FEATURE_NAMES]
            # Simple label: 5-bar forward return
            future_ret = (candles[i + 5].close / candles[i].close - 1) * 100
            if future_ret > 3:
                label = 1  # buy
            elif future_ret < -3:
                label = 2  # sell
            else:
                label = 0  # hold
            all_X.append(row)
            all_y.append(label)

    if not all_X:
        return {"error": "无法构建特征数据"}

    X_test = np.array(all_X)
    y_test = np.array(all_y)

    t0 = time.time()
    # LightGBM Booster returns probabilities directly
    if hasattr(lgb_model, 'predict_proba'):
        y_proba = lgb_model.predict_proba(X_test)
        y_pred = y_proba.argmax(axis=1)
    else:
        # Raw Booster — predict returns (n_samples, n_classes) probabilities
        y_proba = lgb_model.predict(X_test)
        if y_proba.ndim == 1:
            # Binary or reshape needed
            y_pred = (y_proba > 0.5).astype(int)
            y_proba = np.column_stack([1 - y_proba, y_proba])
        else:
            y_pred = y_proba.argmax(axis=1)
    infer_time = time.time() - t0

    target_names = ["hold", "buy", "sell"]
    report = classification_report(y_test, y_pred, target_names=target_names,
                                   output_dict=True, zero_division=0)
    cm = confusion_matrix(y_test, y_pred)

    max_probs = y_proba.max(axis=1)
    correct_mask = y_pred == y_test

    return {
        "model": "lightgbm",
        "model_file": str(model_path),
        "device": "cpu",
        "test_samples": len(y_test),
        "accuracy": round(report["accuracy"], 4),
        "report": report,
        "confusion_matrix": cm.tolist(),
        "class_distribution": {
            "hold": int((y_test == 0).sum()),
            "buy": int((y_test == 1).sum()),
            "sell": int((y_test == 2).sum()),
        },
        "inference_time_ms": round(infer_time * 1000, 1),
        "samples_per_sec": round(len(y_test) / infer_time),
        "avg_confidence": round(float(max_probs.mean()) * 100, 1),
        "confidence_when_correct": round(float(max_probs[correct_mask].mean()) * 100, 1) if correct_mask.any() else 0,
        "confidence_when_wrong": round(float(max_probs[~correct_mask].mean()) * 100, 1) if (~correct_mask).any() else 0,
    }


def eval_rl_ppo(candle_lists: list):
    """Evaluate RL-PPO model by running it on test stocks and measuring trading performance."""
    from app.services.rl_strategy import TradingEnv
    from app.services.ai_strategy import _MODEL_DIR

    model_path = _MODEL_DIR / "ai_rl.zip"
    if not model_path.exists():
        return {"error": f"RL 模型未训练: {model_path}"}

    from stable_baselines3 import PPO
    model = PPO.load(str(model_path))

    t0 = time.time()
    results = []
    for candles in candle_lists:
        if len(candles) < 150:
            continue
        # Use last 30% as "test" period (unseen by model if stocks differ)
        test_start = int(len(candles) * 0.7)
        test_candles = candles[test_start:]
        if len(test_candles) < 60:
            continue

        env = TradingEnv(test_candles)
        obs, _ = env.reset()
        actions = []
        while True:
            action, _ = model.predict(obs, deterministic=True)
            actions.append(int(action))
            obs, reward, terminated, truncated, info = env.step(int(action))
            if terminated or truncated:
                break

        if env.trades:
            results.append({
                "trades": len(env.trades),
                "total_pnl": round(env.total_pnl, 2),
                "win_trades": sum(1 for t in env.trades if t["pnl"] > 0),
                "actions": actions,
            })
    infer_time = time.time() - t0

    if not results:
        return {"error": "无有效回测结果"}

    total_trades = sum(r["trades"] for r in results)
    total_wins = sum(r["win_trades"] for r in results)
    all_pnl = [r["total_pnl"] for r in results]
    win_rate = total_wins / total_trades if total_trades > 0 else 0

    # Action distribution
    all_actions = []
    for r in results:
        all_actions.extend(r["actions"])
    action_counts = {0: 0, 1: 0, 2: 0}
    for a in all_actions:
        action_counts[a] = action_counts.get(a, 0) + 1
    total_actions = len(all_actions)

    profitable_stocks = sum(1 for p in all_pnl if p > 0)

    return {
        "model": "rl_ppo",
        "model_file": str(model_path),
        "device": str(model.device),
        "test_stocks": len(results),
        "total_trades": total_trades,
        "win_trades": total_wins,
        "win_rate": round(win_rate, 4),
        "avg_pnl_per_stock": round(float(np.mean(all_pnl)), 2),
        "median_pnl": round(float(np.median(all_pnl)), 2),
        "max_pnl": round(float(np.max(all_pnl)), 2),
        "min_pnl": round(float(np.min(all_pnl)), 2),
        "profitable_stocks": profitable_stocks,
        "profitable_rate": round(profitable_stocks / len(results), 4),
        "action_distribution": {
            "hold": f"{action_counts.get(0,0)} ({action_counts.get(0,0)/total_actions*100:.1f}%)",
            "buy": f"{action_counts.get(1,0)} ({action_counts.get(1,0)/total_actions*100:.1f}%)",
            "sell": f"{action_counts.get(2,0)} ({action_counts.get(2,0)/total_actions*100:.1f}%)",
        },
        "inference_time_ms": round(infer_time * 1000, 1),
        "samples_per_sec": round(total_actions / infer_time) if infer_time > 0 else 0,
    }


def run_backtest_batch(candle_lists: list, model_type: str, n_stocks: int = 10):
    """Run backtest on multiple stocks and aggregate results."""
    from app.services.ai_strategy import ai_backtest

    results = []
    tested = 0
    for candles in candle_lists[:n_stocks * 2]:  # try more, skip failures
        if tested >= n_stocks:
            break
        if len(candles) < 200:
            continue
        try:
            r = ai_backtest(candles, model_type=model_type)
            if "error" not in r and r.get("total_trades", 0) > 0:
                results.append(r)
                tested += 1
        except Exception:
            continue

    if not results:
        return {"error": "回测无有效结果"}

    # Aggregate
    total_trades = sum(r["total_trades"] for r in results)
    win_trades = sum(r.get("win_trades", 0) for r in results)
    avg_return = np.mean([r.get("total_return_pct", 0) for r in results])
    avg_benchmark = np.mean([r.get("benchmark_return_pct", 0) for r in results])
    avg_sharpe = np.mean([r.get("sharpe_ratio", 0) for r in results])
    avg_max_dd = np.mean([r.get("max_drawdown_pct", 0) for r in results])

    return {
        "model": model_type,
        "stocks_tested": len(results),
        "total_trades": total_trades,
        "win_rate": round(win_trades / total_trades * 100, 1) if total_trades > 0 else 0,
        "avg_return_pct": round(float(avg_return), 2),
        "avg_benchmark_pct": round(float(avg_benchmark), 2),
        "alpha_pct": round(float(avg_return - avg_benchmark), 2),
        "avg_sharpe": round(float(avg_sharpe), 2),
        "avg_max_drawdown_pct": round(float(avg_max_dd), 2),
    }


def print_eval_result(result: dict):
    """Pretty-print evaluation result."""
    if "error" in result:
        print(f"\n  ✗ {result.get('model', '?')}: {result['error']}")
        return

    model = result["model"]

    # RL model has different metrics
    if model == "rl_ppo":
        print(f"\n{'═'*60}")
        print(f"  📊 RL-PPO 模型评估结果 (交易模拟)")
        print(f"{'═'*60}")
        print(f"  模型文件      : {result['model_file']}")
        print(f"  设备          : {result['device']}")
        print(f"  测试股票数    : {result['test_stocks']}")
        print(f"  推理耗时      : {result['inference_time_ms']:.1f} ms ({result['samples_per_sec']:,} steps/s)")
        print(f"")
        print(f"  ┌─────────────────────────────────────────────┐")
        print(f"  │  胜率      : {result['win_rate']*100:.1f}% ({result['win_trades']}/{result['total_trades']} trades) │")
        print(f"  │  盈利股比例: {result['profitable_rate']*100:.1f}% ({result['profitable_stocks']}/{result['test_stocks']} stocks)  │")
        print(f"  └─────────────────────────────────────────────┘")
        print(f"")
        print(f"  收益统计 (每只股票):")
        print(f"    平均收益   : {result['avg_pnl_per_stock']:+.2f} 点")
        print(f"    中位收益   : {result['median_pnl']:+.2f} 点")
        print(f"    最大收益   : {result['max_pnl']:+.2f} 点")
        print(f"    最大亏损   : {result['min_pnl']:+.2f} 点")
        print(f"")
        print(f"  动作分布:")
        for action, desc in result["action_distribution"].items():
            print(f"    {action:<6s}: {desc}")
        print(f"{'═'*60}")
        return

    acc = result["accuracy"]
    report = result["report"]

    print(f"\n{'═'*60}")
    print(f"  📊 {model.upper()} 模型评估结果")
    print(f"{'═'*60}")
    print(f"  模型文件  : {result['model_file']}")
    print(f"  设备      : {result['device']}")
    print(f"  测试样本  : {result['test_samples']:,}")
    print(f"  推理耗时  : {result['inference_time_ms']:.1f} ms ({result['samples_per_sec']:,} samples/s)")
    print(f"")
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │  Overall Accuracy : {acc:.4f} ({acc*100:.1f}%){'':15s}│")
    print(f"  └─────────────────────────────────────────────┘")
    print(f"")
    print(f"  {'类别':<8s} {'Precision':>10s} {'Recall':>8s} {'F1':>8s} {'Support':>9s}")
    print(f"  {'─'*48}")
    for cls in ["hold", "buy", "sell"]:
        r = report[cls]
        print(f"  {cls:<8s} {r['precision']:>10.4f} {r['recall']:>8.4f} {r['f1-score']:>8.4f} {r['support']:>9.0f}")
    print(f"  {'─'*48}")

    # Confusion matrix
    cm = result["confusion_matrix"]
    print(f"\n  混淆矩阵 (行=真实, 列=预测):")
    print(f"  {'':8s} {'hold':>8s} {'buy':>8s} {'sell':>8s}")
    labels = ["hold", "buy", "sell"]
    for i, row in enumerate(cm):
        print(f"  {labels[i]:<8s} {row[0]:>8d} {row[1]:>8d} {row[2]:>8d}")

    # Class distribution
    dist = result["class_distribution"]
    total = sum(dist.values())
    print(f"\n  测试集分布: hold={dist['hold']}({dist['hold']/total*100:.0f}%) "
          f"buy={dist['buy']}({dist['buy']/total*100:.0f}%) "
          f"sell={dist['sell']}({dist['sell']/total*100:.0f}%)")

    # Confidence analysis
    print(f"\n  置信度分析:")
    print(f"    平均置信度         : {result['avg_confidence']:.1f}%")
    print(f"    预测正确时置信度   : {result['confidence_when_correct']:.1f}%")
    print(f"    预测错误时置信度   : {result['confidence_when_wrong']:.1f}%")
    gap = result['confidence_when_correct'] - result['confidence_when_wrong']
    calibration = "✓ 良好" if gap > 5 else "⚠ 需校准" if gap > 0 else "✗ 差"
    print(f"    置信度区分度 (gap) : {gap:.1f}% → {calibration}")
    print(f"{'═'*60}")


def print_backtest_result(result: dict):
    """Pretty-print backtest result."""
    if "error" in result:
        print(f"\n  ✗ 回测失败: {result['error']}")
        return

    model = result["model"]
    print(f"\n  📈 {model.upper()} 回测汇总 ({result['stocks_tested']} 只股票)")
    print(f"  {'─'*45}")
    print(f"    总交易次数    : {result['total_trades']}")
    print(f"    胜率          : {result['win_rate']:.1f}%")
    print(f"    平均收益      : {result['avg_return_pct']:+.2f}%")
    print(f"    基准收益      : {result['avg_benchmark_pct']:+.2f}%")
    alpha = result['alpha_pct']
    alpha_flag = "🟢" if alpha > 0 else "🔴"
    print(f"    Alpha (超额)  : {alpha:+.2f}% {alpha_flag}")
    print(f"    夏普率        : {result['avg_sharpe']:.2f}")
    print(f"    最大回撤      : {result['avg_max_drawdown_pct']:.2f}%")
    print(f"  {'─'*45}")


def print_comparison(results: list[dict]):
    """Print comparison table across models."""
    valid = [r for r in results if "error" not in r]
    if not valid:
        return

    # Separate classification models from RL
    cls_models = [r for r in valid if "accuracy" in r]
    rl_models = [r for r in valid if "win_rate" in r and "accuracy" not in r]

    if cls_models:
        print(f"\n\n{'═'*70}")
        print(f"  🏆 分类模型对比排行")
        print(f"{'═'*70}")
        print(f"  {'模型':<14s} {'Accuracy':>9s} {'Buy-P':>7s} {'Buy-R':>7s} {'Sell-P':>7s} {'Sell-R':>7s} {'速度':>10s}")
        print(f"  {'─'*66}")

        cls_models.sort(key=lambda r: r["accuracy"], reverse=True)
        for r in cls_models:
            rep = r["report"]
            print(f"  {r['model']:<14s} "
                  f"{r['accuracy']:>9.4f} "
                  f"{rep['buy']['precision']:>7.4f} "
                  f"{rep['buy']['recall']:>7.4f} "
                  f"{rep['sell']['precision']:>7.4f} "
                  f"{rep['sell']['recall']:>7.4f} "
                  f"{r['samples_per_sec']:>7,}s/s")

        print(f"  {'─'*66}")
        best = cls_models[0]
        print(f"\n  🥇 最佳模型: {best['model']} (Accuracy={best['accuracy']:.4f})")

        buy_best = max(cls_models, key=lambda r: r["report"]["buy"]["precision"])
        sell_best = max(cls_models, key=lambda r: r["report"]["sell"]["precision"])
        print(f"  💡 买入信号最精准: {buy_best['model']} (buy precision={buy_best['report']['buy']['precision']:.4f})")
        print(f"  💡 卖出信号最精准: {sell_best['model']} (sell precision={sell_best['report']['sell']['precision']:.4f})")

    if rl_models:
        print(f"\n  {'─'*50}")
        print(f"  🎮 RL 模型:")
        for r in rl_models:
            print(f"    {r['model']}: 胜率={r['win_rate']*100:.1f}% 盈利股={r['profitable_rate']*100:.1f}% "
                  f"平均收益={r['avg_pnl_per_stock']:+.2f}点")

    print(f"{'═'*70}")


def main():
    parser = argparse.ArgumentParser(
        description="PivotLab 模型评估 — 测试集推理 + 指标分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python eval_cli.py --model transformer --stocks 100
  python eval_cli.py --model all --stocks 200
  python eval_cli.py --model all --stocks 200 --backtest
        """,
    )
    parser.add_argument("--model", "-m", required=True,
                        choices=ALL_MODELS + ["all"],
                        help="模型类型 (or 'all')")
    parser.add_argument("--stocks", "-s", type=int, default=100,
                        help="评估用股票数量 (default: 100)")
    parser.add_argument("--gpu", "-g", type=int, default=None,
                        help="GPU index (-1 for CPU)")
    parser.add_argument("--backtest", "-b", action="store_true",
                        help="同时跑回测对比")
    parser.add_argument("--backtest-stocks", type=int, default=10,
                        help="回测使用的股票数量 (default: 10)")
    parser.add_argument("--min-days", type=int, default=200,
                        help="每只股票最少天数 (default: 200)")
    args = parser.parse_args()

    if args.gpu is not None:
        if args.gpu < 0:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)

    # Load data
    candle_lists = load_data(args.stocks, args.min_days)

    models = ALL_MODELS if args.model == "all" else [args.model]

    # Check which models are trained
    trained = []
    for mt in models:
        keys = {"transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm",
                "lightgbm": "ai_lgb", "rl_ppo": "ai_rl"}
        key = keys.get(mt, f"ai_{mt}")
        if mt == "lightgbm":
            path = _MODEL_DIR / f"{key}.pkl"
        elif mt == "rl_ppo":
            path = _MODEL_DIR / f"{key}.zip"
        else:
            path = _MODEL_DIR / f"{key}.pt"
        if path.exists():
            trained.append(mt)
            logger.info("✓ %s 模型已找到: %s", mt, path)
        else:
            logger.warning("✗ %s 模型未训练: %s", mt, path)

    if not trained:
        print("\n  没有找到已训练的模型！请先运行训练:")
        print("    docker exec pivotlab python /app/backend/train_cli.py --model all --stocks 200 --epochs 50 --num-gpus 3")
        sys.exit(1)

    # Evaluate
    results = []
    t_total = time.time()

    for mt in trained:
        logger.info("Evaluating %s ...", mt)
        if mt in PYTORCH_MODELS:
            r = eval_pytorch_model(mt, candle_lists)
        elif mt == "lightgbm":
            r = eval_lightgbm(candle_lists)
        elif mt == "rl_ppo":
            r = eval_rl_ppo(candle_lists)
        else:
            r = {"model": mt, "error": "不支持的模型类型"}
        results.append(r)
        print_eval_result(r)

    # Comparison table
    if len(results) > 1:
        print_comparison(results)

    # Backtest
    if args.backtest:
        print(f"\n\n{'═'*60}")
        print(f"  📈 回测对比 (每模型 {args.backtest_stocks} 只股票)")
        print(f"{'═'*60}")

        bt_results = []
        for mt in trained:
            logger.info("Backtesting %s ...", mt)
            bt = run_backtest_batch(candle_lists, mt, n_stocks=args.backtest_stocks)
            bt_results.append(bt)
            print_backtest_result(bt)

        # Backtest comparison
        valid_bt = [r for r in bt_results if "error" not in r]
        if len(valid_bt) > 1:
            print(f"\n  {'模型':<14s} {'胜率':>6s} {'收益':>8s} {'Alpha':>8s} {'夏普':>6s} {'回撤':>8s}")
            print(f"  {'─'*54}")
            valid_bt.sort(key=lambda r: r["alpha_pct"], reverse=True)
            for r in valid_bt:
                print(f"  {r['model']:<14s} {r['win_rate']:>5.1f}% {r['avg_return_pct']:>+7.2f}% "
                      f"{r['alpha_pct']:>+7.2f}% {r['avg_sharpe']:>5.2f} {r['avg_max_drawdown_pct']:>7.2f}%")
            best_bt = valid_bt[0]
            print(f"\n  🥇 回测最佳: {best_bt['model']} (Alpha={best_bt['alpha_pct']:+.2f}%)")

    elapsed = time.time() - t_total
    print(f"\n  总评估耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
