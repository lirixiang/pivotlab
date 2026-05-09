#!/usr/bin/env python3
"""Standalone CLI training script — supports single-GPU and multi-GPU DDP.

Usage (inside container):
    # 单 GPU 训练
    python train_cli.py --model transformer --stocks 200 --epochs 50 --gpu 0

    # 多 GPU DDP 训练 (Ray TorchTrainer)
    python train_cli.py --model transformer --stocks 200 --epochs 50 --num-gpus 3

    # 6 卡 DDP
    python train_cli.py --model lstm --stocks 500 --epochs 30 --num-gpus 6

    # CPU 训练 (LightGBM)
    python train_cli.py --model lightgbm --stocks 500

    # RL-PPO
    python train_cli.py --model rl_ppo --stocks 100 --epochs 100 --gpu 0

    # 全部 5 个模型顺序训练 (各用 1 GPU)
    python train_cli.py --model all --stocks 200 --epochs 50 --gpu 0

    # 全部 5 个 PyTorch 模型用 DDP
    python train_cli.py --model all --stocks 200 --epochs 50 --num-gpus 3

Usage (from host):
    docker exec pivotlab python /app/backend/train_cli.py --model transformer --stocks 100 --epochs 20 --num-gpus 3
"""
import argparse
import json
import os
import sys
import time
import logging

# Ensure app package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("train_cli")

ALL_MODELS = ["lightgbm", "transformer", "lstm", "cnn_lstm", "rl_ppo"]


def load_data(max_stocks: int, min_days: int):
    """Load candle data from PostgreSQL.

    Selects stocks with the highest average daily volume (last 6 months),
    filtered to have at least min_days of history. Excludes ST stocks.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.services.data_provider import _cache_read

    db_url = os.environ.get("DATABASE_URL", "")
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    if not sync_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    engine = create_engine(sync_url)
    with Session(engine) as session:
        rows = session.execute(text(
            "SELECT dc.code "
            "FROM daily_candles dc "
            "LEFT JOIN stocks s ON dc.code = s.code "
            "WHERE (s.is_st IS NULL OR s.is_st = false) "
            "GROUP BY dc.code "
            "HAVING COUNT(*) >= :min_days "
            "ORDER BY AVG(CASE WHEN dc.trade_date >= (CURRENT_DATE - INTERVAL '180 days')::text "
            "                  THEN dc.volume ELSE NULL END) DESC NULLS LAST "
            "LIMIT :limit"
        ), {"min_days": min_days, "limit": max_stocks}).fetchall()
        codes = [r[0] for r in rows]
    engine.dispose()

    if not codes:
        logger.error("No stocks found with >= %d days of data", min_days)
        sys.exit(1)

    candle_lists = [
        c for code in codes
        if (c := _cache_read(code, limit=730)) and len(c) >= min_days
    ]
    logger.info("Loaded %d/%d stocks (min_days=%d, sorted by volume)", len(candle_lists), len(codes), min_days)
    return candle_lists


def make_progress_cb(model_type: str):
    """Create a CLI progress callback that prints to stdout."""
    def progress_cb(pct, msg):
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  {model_type:12s} |{bar}| {int(pct):3d}% {msg}", end="", flush=True)
        if pct >= 100:
            print()
    return progress_cb


def train_one(model_type: str, candle_lists: list, epochs: int, label_method: str, pct_threshold: float):
    """Train a single model, return result dict."""
    from app.services.ai_strategy import train_model

    progress_cb = make_progress_cb(model_type)
    t0 = time.time()

    logger.info("Training %s (epochs=%d, stocks=%d) ...", model_type, epochs, len(candle_lists))
    result = train_model(
        candle_lists,
        model_type=model_type,
        label_method=label_method,
        pct_threshold=pct_threshold,
        epochs=epochs,
        progress_cb=progress_cb,
    )

    elapsed = time.time() - t0
    result["elapsed_seconds"] = round(elapsed, 1)
    result["stocks_used"] = len(candle_lists)
    return result


def build_dataset_for_ddp(candle_lists: list, model_type: str, label_method: str, pct_threshold: float):
    """Build dataset once for DDP — all workers share identical data."""
    import numpy as np
    import pickle
    from app.services.ai_strategy import build_sequence_dataset, NUM_FEATURES, _MODEL_DIR

    model_keys = {"transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm"}
    model_key = model_keys[model_type]

    all_X, all_y = [], []
    for candles in candle_lists:
        if len(candles) < 120:
            continue
        X, y = build_sequence_dataset(candles, label_method, pct_threshold)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return None

    X = np.concatenate(all_X)
    y = np.concatenate(all_y)

    mean = X.reshape(-1, NUM_FEATURES).mean(axis=0)
    std = X.reshape(-1, NUM_FEATURES).std(axis=0) + 1e-8
    X = (X - mean) / std

    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    with open(norm_path, "wb") as f:
        pickle.dump({"mean": mean, "std": std}, f)

    return {
        "X": X, "y": y, "model_key": model_key,
        "candle_count": len(candle_lists), "total_codes": len(candle_lists),
        "MODEL_DIR": _MODEL_DIR,
    }


def _ddp_train_fn(config: dict):
    """Per-worker DDP training function for Ray TorchTrainer (CLI)."""
    import ray
    import ray.train as rt
    import ray.train.torch
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    import numpy as np
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    model_type = config["model_type"]
    epochs = config["epochs"]
    batch_size = config.get("batch_size", 64)
    lr = config.get("lr", 1e-3)

    ctx = rt.get_context()
    is_rank0 = ctx.get_world_rank() == 0
    world_size = ctx.get_world_size()

    # Retrieve pre-built dataset from object store
    ds_ref = config.get("_dataset_ref")
    if ds_ref is None:
        rt.report({"loss": 0, "accuracy": 0, "epoch": 0, "failed": True})
        return

    ds = ray.get(ds_ref)
    if ds is None:
        rt.report({"loss": 0, "accuracy": 0, "epoch": 0, "failed": True})
        return

    X, y = ds["X"], ds["y"]
    model_key = ds["model_key"]
    MODEL_DIR = ds["MODEL_DIR"]
    total = len(y)

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    class_weights = torch.ones(3)
    for cls in [0, 1, 2]:
        cnt = (y_train == cls).sum()
        if cnt > 0:
            class_weights[cls] = total / (3 * cnt)

    train_ds = TensorDataset(torch.from_numpy(X_train).float(), torch.from_numpy(y_train).long())
    test_ds = TensorDataset(torch.from_numpy(X_test).float(), torch.from_numpy(y_test).long())
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)
    test_dl = DataLoader(test_ds, batch_size=batch_size, drop_last=True)

    train_dl = ray.train.torch.prepare_data_loader(train_dl)
    test_dl = ray.train.torch.prepare_data_loader(test_dl)

    from app.services.ai_strategy import _build_transformer, _build_lstm, _build_cnn_lstm
    builders = {"transformer": _build_transformer, "lstm": _build_lstm, "cnn_lstm": _build_cnn_lstm}
    model = builders[model_type]()()
    model = ray.train.torch.prepare_model(model)

    device = ray.train.torch.get_device()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss(weight=class_weights.to(device))

    best_acc = 0.0
    best_state = None

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        n_samples = 0
        for xb, yb in train_dl:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item() * len(xb)
            n_samples += len(xb)
        scheduler.step()

        avg_loss = epoch_loss / max(n_samples, 1)

        model.eval()
        correct = total_test = 0
        with torch.no_grad():
            for xb, yb in test_dl:
                xb, yb = xb.to(device), yb.to(device)
                preds = model(xb).argmax(dim=1)
                correct += (preds == yb).sum().item()
                total_test += len(yb)
        acc = correct / total_test if total_test > 0 else 0

        if acc > best_acc:
            best_acc = acc
            raw_model = model.module if hasattr(model, "module") else model
            best_state = {k: v.cpu().clone() for k, v in raw_model.state_dict().items()}

        if is_rank0:
            pct = 10 + int((epoch + 1) / epochs * 80)
            bar_len = 30
            filled = int(bar_len * pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  {model_type:12s} |{bar}| {pct:3d}% Epoch {epoch+1}/{epochs} loss={avg_loss:.4f} acc={acc:.4f} (DDP×{world_size})", end="", flush=True)

        # rt.report is a barrier — ALL workers must call it every epoch
        rt.report({"loss": avg_loss, "accuracy": acc, "epoch": epoch + 1})

    # Save best model (rank 0 only)
    if is_rank0 and best_state is not None:
        import torch
        torch.save(best_state, MODEL_DIR / f"{model_key}.pt")

        from sklearn.metrics import classification_report
        raw_model = model.module if hasattr(model, "module") else model
        raw_model.load_state_dict(best_state)
        raw_model.eval()
        test_dl_local = DataLoader(test_ds, batch_size=batch_size)
        all_preds, all_true = [], []
        with torch.no_grad():
            for xb, yb in test_dl_local:
                xb = xb.to(device)
                all_preds.extend(raw_model(xb).argmax(dim=1).cpu().numpy().tolist())
                all_true.extend(yb.cpu().numpy().tolist())
        report = classification_report(all_true, all_preds,
                                       target_names=["hold", "buy", "sell"],
                                       output_dict=True, zero_division=0)
        result = {
            "model": model_type, "device": str(device),
            "samples": total,
            "accuracy": round(best_acc, 4),
            "buy_precision": round(report["buy"]["precision"], 4),
            "buy_recall": round(report["buy"]["recall"], 4),
            "sell_precision": round(report["sell"]["precision"], 4),
            "sell_recall": round(report["sell"]["recall"], 4),
            "epochs": epochs, "train_loss_last": round(avg_loss, 4),
            "distributed": True, "num_workers": world_size,
        }
        print(f"\n  DDP training complete: acc={best_acc:.4f}")


def train_one_ddp(model_type: str, candle_lists: list, epochs: int,
                  label_method: str, pct_threshold: float, num_gpus: int):
    """Train a single model using Ray DDP. Returns result dict."""
    import ray
    from ray.train import RunConfig, ScalingConfig
    from ray.train.torch import TorchTrainer

    logger.info("Building dataset for DDP (%d GPUs) ...", num_gpus)
    ds = build_dataset_for_ddp(candle_lists, model_type, label_method, pct_threshold)
    if ds is None:
        return {"error": "数据不足，无法构建训练集"}

    ds_ref = ray.put(ds)
    config = {
        "model_type": model_type,
        "epochs": epochs,
        "_dataset_ref": ds_ref,
    }

    trainer = TorchTrainer(
        train_loop_per_worker=_ddp_train_fn,
        train_loop_config=config,
        scaling_config=ScalingConfig(
            num_workers=num_gpus,
            use_gpu=True,
            resources_per_worker={"GPU": 1},
        ),
        run_config=RunConfig(
            name=f"cli_{model_type}_{num_gpus}gpu",
            storage_path="/tmp/ray_results",
        ),
    )

    t0 = time.time()
    ray_result = trainer.fit()
    elapsed = time.time() - t0

    # Extract final result from the last report
    metrics = ray_result.metrics or {}
    # The last rt.report has the best accuracy from the training loop
    result = {
        "model": model_type,
        "accuracy": round(metrics.get("accuracy", 0), 4),
        "train_loss_last": round(metrics.get("loss", 0), 4),
        "epochs": epochs,
        "samples": len(ds["X"]),
        "distributed": True, "num_workers": num_gpus,
        "elapsed_seconds": round(elapsed, 1),
        "stocks_used": len(candle_lists),
    }

    # Try to load detailed metrics from saved model
    from pathlib import Path
    model_keys = {"transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm"}
    model_key = model_keys[model_type]
    model_path = Path("/app/backend/../models") / f"{model_key}.pt"
    if not model_path.exists():
        from app.services.ai_strategy import _MODEL_DIR
        model_path = _MODEL_DIR / f"{model_key}.pt"
    if model_path.exists():
        import torch
        from app.services.ai_strategy import _build_transformer, _build_lstm, _build_cnn_lstm
        import numpy as np
        from torch.utils.data import DataLoader, TensorDataset

        builders = {"transformer": _build_transformer, "lstm": _build_lstm, "cnn_lstm": _build_cnn_lstm}
        raw_model = builders[model_type]()()
        raw_model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
        raw_model.eval()

        X, y = ds["X"], ds["y"]
        split = int(len(X) * 0.8)
        X_test, y_test = X[split:], y[split:]
        test_ds_local = TensorDataset(torch.from_numpy(X_test).float(), torch.from_numpy(y_test).long())
        test_dl_local = DataLoader(test_ds_local, batch_size=64)

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        raw_model = raw_model.to(device)
        all_preds, all_true = [], []
        with torch.no_grad():
            for xb, yb in test_dl_local:
                xb = xb.to(device)
                all_preds.extend(raw_model(xb).argmax(dim=1).cpu().numpy().tolist())
                all_true.extend(yb.cpu().numpy().tolist())

        from sklearn.metrics import classification_report
        report = classification_report(all_true, all_preds,
                                       target_names=["hold", "buy", "sell"],
                                       output_dict=True, zero_division=0)
        correct = sum(p == t for p, t in zip(all_preds, all_true))
        result["accuracy"] = round(correct / len(all_true), 4) if all_true else 0
        result["buy_precision"] = round(report["buy"]["precision"], 4)
        result["buy_recall"] = round(report["buy"]["recall"], 4)
        result["sell_precision"] = round(report["sell"]["precision"], 4)
        result["sell_recall"] = round(report["sell"]["recall"], 4)
        result["device"] = str(device)

    return result


def print_result(model_type: str, result: dict):
    """Pretty-print training result."""
    acc = result.get("accuracy", result.get("mean_reward", "N/A"))
    elapsed = result.get("elapsed_seconds", "?")
    samples = result.get("samples", result.get("total_timesteps", "?"))

    print(f"\n{'─'*50}")
    print(f"  {model_type} — Done in {elapsed}s")
    print(f"  Accuracy/Reward : {acc}")
    print(f"  Samples         : {samples}")

    for key in ["buy_precision", "buy_recall", "sell_precision", "sell_recall"]:
        if key in result:
            print(f"  {key:18s}: {result[key]}")

    device = result.get("device", "cpu")
    workers = result.get("num_workers", 1)
    distributed = result.get("distributed", False)
    mode = f"DDP×{workers}" if distributed else device
    print(f"  Device          : {mode}")
    print(f"{'─'*50}\n")


def main():
    parser = argparse.ArgumentParser(
        description="PivotLab AI Model Training CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python train_cli.py --model transformer --stocks 200 --epochs 50 --gpu 0
  python train_cli.py --model lightgbm --stocks 500
  python train_cli.py --model all --stocks 100 --epochs 20 --gpu 0
        """,
    )
    parser.add_argument("--model", "-m", required=True,
                        choices=ALL_MODELS + ["all"],
                        help="Model type to train (or 'all' for all 5)")
    parser.add_argument("--stocks", "-s", type=int, default=200,
                        help="Max number of stocks to load (default: 200)")
    parser.add_argument("--epochs", "-e", type=int, default=50,
                        help="Training epochs (default: 50)")
    parser.add_argument("--gpu", "-g", type=int, default=None,
                        help="GPU index to use for single-GPU (default: auto). -1 for CPU")
    parser.add_argument("--num-gpus", "-n", type=int, default=1,
                        help="Number of GPUs for DDP training (default: 1 = no DDP)")
    parser.add_argument("--min-days", type=int, default=200,
                        help="Minimum days of data per stock (default: 200)")
    parser.add_argument("--label-method", type=str, default="zigzag",
                        help="Labeling method (default: zigzag)")
    parser.add_argument("--pct-threshold", type=float, default=5.0,
                        help="Percentage threshold for labeling (default: 5.0)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Save results to JSON file")
    args = parser.parse_args()

    use_ddp = args.num_gpus > 1

    # Set GPU (only for single-GPU mode)
    if not use_ddp and args.gpu is not None:
        if args.gpu < 0:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            logger.info("Forcing CPU mode")
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)
            logger.info("Using GPU %d", args.gpu)

    # Initialize Ray for DDP
    if use_ddp:
        import ray
        try:
            ray.init(address="auto")
            logger.info("Connected to existing Ray cluster for DDP (%d GPUs)", args.num_gpus)
        except ConnectionError:
            ray.init(num_gpus=args.num_gpus)
            logger.info("Started new Ray instance with %d GPUs for DDP", args.num_gpus)

    # Load data once
    candle_lists = load_data(args.stocks, args.min_days)

    # Determine which models to train
    models = ALL_MODELS if args.model == "all" else [args.model]

    results = {}
    t_total = time.time()

    DDP_MODELS = {"transformer", "lstm", "cnn_lstm"}

    for mt in models:
        try:
            if use_ddp and mt in DDP_MODELS:
                logger.info("Training %s with DDP (%d GPUs) ...", mt, args.num_gpus)
                result = train_one_ddp(mt, candle_lists, args.epochs,
                                       args.label_method, args.pct_threshold, args.num_gpus)
            else:
                result = train_one(mt, candle_lists, args.epochs, args.label_method, args.pct_threshold)
            results[mt] = result
            print_result(mt, result)
        except Exception as e:
            logger.exception("Training %s failed", mt)
            results[mt] = {"error": str(e)}
            print(f"\n  ✗ {mt} FAILED: {e}\n")

    # Summary
    elapsed_total = time.time() - t_total
    mode_str = f"DDP×{args.num_gpus}" if use_ddp else "single-GPU"
    print(f"{'═'*50}")
    print(f"  Total: {len(results)} model(s) in {elapsed_total:.1f}s ({mode_str})")
    for mt, r in results.items():
        status = "✓" if "error" not in r else "✗"
        acc = r.get("accuracy", r.get("mean_reward", r.get("error", "?")))
        print(f"    {status} {mt:12s}  {acc}")
    print(f"{'═'*50}")

    # Save to file
    if args.output:
        def _clean(obj):
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_clean(v) for v in obj]
            if hasattr(obj, "item"):
                return obj.item()
            return obj
        with open(args.output, "w") as f:
            json.dump(_clean(results), f, indent=2, ensure_ascii=False)
        logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
