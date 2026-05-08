"""Ray-based distributed training manager.

Architecture:
  - ProgressActor: Ray actor holding all task progress in memory (replaces file IPC)
  - train_torch_remote: @ray.remote(num_gpus=1) for single-GPU PyTorch training
  - _torch_ddp_train_fn + TorchTrainer: multi-GPU DDP via Ray Train
  - train_lightgbm_remote: @ray.remote(num_cpus=4) for LightGBM (CPU)
  - train_rl_remote: @ray.remote(num_gpus=1) for RL-PPO
  - submit_training: unified entry — num_gpus=1 → single GPU, >1 → DDP, "all" → parallel
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

import ray

logger = logging.getLogger(__name__)

NUM_GPUS = int(os.environ.get("NUM_GPUS", "6"))


# ─────────────────── Progress Actor ───────────────────


@ray.remote
class ProgressActor:
    """Shared in-memory progress store, accessible from any Ray task/actor."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    def update(self, task_id: str, **kw):
        if task_id not in self._tasks:
            self._tasks[task_id] = {"task_id": task_id}
        self._tasks[task_id].update(kw)

    def get(self, task_id: str) -> dict | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[dict]:
        return list(self._tasks.values())

    def remove(self, task_id: str):
        self._tasks.pop(task_id, None)

    def clear_finished(self) -> int:
        to_remove = [
            tid for tid, t in self._tasks.items()
            if t.get("status") in ("completed", "failed", "cancelled")
        ]
        for tid in to_remove:
            del self._tasks[tid]
        return len(to_remove)


# ─── Singleton accessor ───
_progress_actor = None


def get_progress_actor() -> ProgressActor:
    global _progress_actor
    if _progress_actor is None:
        _progress_actor = ProgressActor.options(
            name="progress_actor", lifetime="detached", get_if_exists=True
        ).remote()
    return _progress_actor


# ─────────────────── Data Loading (shared) ───────────────────


def _load_candles_from_db(max_stocks: int, min_days: int) -> tuple[list, int]:
    """Load candle data from DB. Runs inside Ray workers."""
    import sys
    sys.path.insert(0, "/app/backend")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "+psycopg2")
    engine = create_engine(db_url)

    with Session(engine) as session:
        rows = session.execute(text(
            "SELECT code FROM daily_candles "
            "GROUP BY code HAVING COUNT(*) >= :min_days "
            "ORDER BY random() LIMIT :limit"
        ), {"min_days": min_days, "limit": max_stocks}).fetchall()
        codes = [r[0] for r in rows]
    engine.dispose()

    if not codes:
        return [], 0

    from app.services.data_provider import _cache_read
    candle_lists = [c for code in codes if (c := _cache_read(code, limit=730)) and len(c) >= min_days]
    return candle_lists, len(codes)


# ─────────────────── PyTorch Training (single GPU per model) ───────────────────


@ray.remote(num_gpus=1)
def train_torch_remote(config: dict):
    """Train a PyTorch model on a single GPU as a Ray remote task."""
    import sys
    sys.path.insert(0, "/app/backend")
    import numpy as np
    import pickle
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    task_id = config["task_id"]
    model_type = config["model_type"]
    epochs = config["epochs"]
    batch_size = config.get("batch_size", 64)
    lr = config.get("lr", 1e-3)
    label_method = config.get("label_method", "zigzag")
    pct_threshold = config.get("pct_threshold", 5.0)

    progress_actor = ray.get_actor("progress_actor")

    try:
        # ── Load data ──
        candle_lists, total_codes = _load_candles_from_db(
            config["max_stocks"], config.get("min_days", 200)
        )
        if not candle_lists:
            ray.get(progress_actor.update.remote(
                task_id, status="failed", progress=0,
                message="数据库中没有足够的K线数据", ended_at=time.time(),
            ))
            return

        ray.get(progress_actor.update.remote(
            task_id, status="training", progress=5,
            message=f"已加载 {len(candle_lists)} 只股票, 开始 {model_type} 训练...",
            codes_used=len(candle_lists), total_codes=total_codes,
        ))

        # ── Build dataset ──
        from app.services.ai_strategy import (
            build_sequence_dataset, NUM_FEATURES, _MODEL_DIR,
            _build_transformer, _build_lstm, _build_cnn_lstm,
        )

        all_X, all_y = [], []
        for candles in candle_lists:
            if len(candles) < 120:
                continue
            X, y = build_sequence_dataset(candles, label_method, pct_threshold)
            if len(X) > 0:
                all_X.append(X)
                all_y.append(y)

        if not all_X:
            ray.get(progress_actor.update.remote(
                task_id, status="failed", progress=0,
                message="数据不足", ended_at=time.time(),
            ))
            return

        X = np.concatenate(all_X)
        y = np.concatenate(all_y)
        total = len(y)
        class_counts = {int(c): int(np.sum(y == c)) for c in [0, 1, 2]}

        # Normalize
        mean = X.reshape(-1, NUM_FEATURES).mean(axis=0)
        std = X.reshape(-1, NUM_FEATURES).std(axis=0) + 1e-8
        X = (X - mean) / std

        model_keys = {"transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm"}
        model_key = model_keys[model_type]

        norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
        with open(norm_path, "wb") as f:
            pickle.dump({"mean": mean, "std": std}, f)

        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        # Class weights
        class_weights = torch.ones(3)
        for cls in [0, 1, 2]:
            cnt = (y_train == cls).sum()
            if cnt > 0:
                class_weights[cls] = total / (3 * cnt)

        train_ds = TensorDataset(
            torch.from_numpy(X_train).float(),
            torch.from_numpy(y_train).long(),
        )
        test_ds = TensorDataset(
            torch.from_numpy(X_test).float(),
            torch.from_numpy(y_test).long(),
        )
        train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        test_dl = DataLoader(test_ds, batch_size=batch_size)

        # ── Build model ──
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        builders = {"transformer": _build_transformer, "lstm": _build_lstm, "cnn_lstm": _build_cnn_lstm}
        model = builders[model_type]()().to(device)

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

            # Evaluate every 5 epochs or last
            acc = 0.0
            if (epoch + 1) % 5 == 0 or epoch == epochs - 1:
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
                    best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

            # Report progress
            pct = 10 + int((epoch + 1) / epochs * 80)
            progress_actor.update.remote(
                task_id, status="training", progress=min(pct, 92),
                message=f"{model_type}: Epoch {epoch+1}/{epochs} loss={avg_loss:.4f} acc={acc:.4f}",
            )

        # Save best model
        if best_state is not None:
            torch.save(best_state, _MODEL_DIR / f"{model_key}.pt")

            from sklearn.metrics import classification_report
            model.load_state_dict(best_state)
            model.eval()
            all_preds, all_true = [], []
            with torch.no_grad():
                for xb, yb in test_dl:
                    xb = xb.to(device)
                    all_preds.extend(model(xb).argmax(dim=1).cpu().numpy().tolist())
                    all_true.extend(yb.cpu().numpy().tolist())
            report = classification_report(all_true, all_preds,
                                           target_names=["hold", "buy", "sell"],
                                           output_dict=True, zero_division=0)
            result = {
                "model": model_type, "device": str(device),
                "samples": total, "class_counts": class_counts,
                "accuracy": round(best_acc, 4),
                "buy_precision": round(report["buy"]["precision"], 4),
                "buy_recall": round(report["buy"]["recall"], 4),
                "sell_precision": round(report["sell"]["precision"], 4),
                "sell_recall": round(report["sell"]["recall"], 4),
                "epochs": epochs, "train_loss_last": round(avg_loss, 4),
                "codes_used": len(candle_lists), "total_market_codes": total_codes,
                "distributed": True, "num_workers": 1,
            }
        else:
            result = {"model": model_type, "accuracy": 0, "message": "no improvement"}

        ray.get(progress_actor.update.remote(
            task_id, status="completed", progress=100,
            message="训练完成", ended_at=time.time(), result=result,
        ))
    except Exception as e:
        logger.exception("%s training failed: %s", model_type, e)
        ray.get(progress_actor.update.remote(
            task_id, status="failed", progress=0,
            message=f"训练失败: {e}", ended_at=time.time(),
        ))


# ─────────────────── PyTorch DDP Training (multi-GPU) ───────────────────


def _build_dataset_common(config: dict):
    """Build dataset — shared by DDP workers. Returns dict or None."""
    import sys
    sys.path.insert(0, "/app/backend")
    import numpy as np
    import pickle
    from app.services.ai_strategy import (
        build_sequence_dataset, NUM_FEATURES, _MODEL_DIR,
    )

    candle_lists, total_codes = _load_candles_from_db(
        config["max_stocks"], config.get("min_days", 200)
    )
    if not candle_lists:
        return None

    label_method = config.get("label_method", "zigzag")
    pct_threshold = config.get("pct_threshold", 5.0)

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

    model_keys = {"transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm"}
    model_key = model_keys[config["model_type"]]

    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    with open(norm_path, "wb") as f:
        pickle.dump({"mean": mean, "std": std}, f)

    return {
        "X": X, "y": y, "model_key": model_key,
        "candle_count": len(candle_lists), "total_codes": total_codes,
        "MODEL_DIR": _MODEL_DIR,
    }


def _torch_ddp_train_fn(config: dict):
    """Per-worker DDP training function for Ray TorchTrainer.

    Each worker gets a shard of data; gradients are synchronized via NCCL.
    Dataset is pre-built and shared via Ray object store to ensure all
    workers see identical data (avoids ORDER BY random() divergence).
    """
    import ray.train as rt
    import ray.train.torch
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    import numpy as np
    import sys
    sys.path.insert(0, "/app/backend")

    model_type = config["model_type"]
    epochs = config["epochs"]
    batch_size = config.get("batch_size", 64)
    lr = config.get("lr", 1e-3)
    task_id = config["task_id"]

    progress_actor = ray.get_actor("progress_actor")
    ctx = rt.get_context()
    is_rank0 = ctx.get_world_rank() == 0
    world_size = ctx.get_world_size()

    # ── Retrieve pre-built dataset from object store ──
    ds_ref = config.get("_dataset_ref")
    if ds_ref is None:
        if is_rank0:
            ray.get(progress_actor.update.remote(
                task_id, status="failed", progress=0,
                message="数据不足", ended_at=time.time(),
            ))
        rt.report({"loss": 0, "accuracy": 0, "epoch": 0, "failed": True})
        return

    ds = ray.get(ds_ref)
    if ds is None:
        if is_rank0:
            ray.get(progress_actor.update.remote(
                task_id, status="failed", progress=0,
                message="数据不足", ended_at=time.time(),
            ))
        rt.report({"loss": 0, "accuracy": 0, "epoch": 0, "failed": True})
        return

    X, y = ds["X"], ds["y"]
    model_key = ds["model_key"]
    MODEL_DIR = ds["MODEL_DIR"]
    total = len(y)
    class_counts = {int(c): int(np.sum(y == c)) for c in [0, 1, 2]}

    if is_rank0:
        ray.get(progress_actor.update.remote(
            task_id, status="training", progress=5,
            message=f"已加载 {ds['candle_count']} 只股票, {model_type} DDP×{world_size} 训练...",
            codes_used=ds["candle_count"], total_codes=ds["total_codes"],
        ))

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    class_weights = torch.ones(3)
    for cls in [0, 1, 2]:
        cnt = (y_train == cls).sum()
        if cnt > 0:
            class_weights[cls] = total / (3 * cnt)

    train_ds = TensorDataset(
        torch.from_numpy(X_train).float(),
        torch.from_numpy(y_train).long(),
    )
    test_ds = TensorDataset(
        torch.from_numpy(X_test).float(),
        torch.from_numpy(y_test).long(),
    )
    # drop_last=True ensures all workers have same number of batches
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)
    test_dl = DataLoader(test_ds, batch_size=batch_size, drop_last=True)

    # ── DDP: prepare data loaders + model ──
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

        # Evaluate every epoch — all workers must participate (distributed test_dl)
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

        pct = 10 + int((epoch + 1) / epochs * 80)
        if is_rank0:
            progress_actor.update.remote(
                task_id, status="training", progress=min(pct, 92),
                message=f"{model_type}: Epoch {epoch+1}/{epochs} loss={avg_loss:.4f} acc={acc:.4f} (DDP×{world_size})",
            )
        # rt.report is a barrier — ALL workers must call it every epoch
        rt.report({"loss": avg_loss, "accuracy": acc, "epoch": epoch + 1})

    # Save best model (rank 0 only)
    if is_rank0 and best_state is not None:
        torch.save(best_state, MODEL_DIR / f"{model_key}.pt")

        from sklearn.metrics import classification_report
        # Use non-distributed evaluation for final metrics
        raw_model = model.module if hasattr(model, "module") else model
        raw_model.load_state_dict(best_state)
        raw_model.eval()
        # Re-create non-distributed test loader for rank0 evaluation
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
            "samples": total, "class_counts": class_counts,
            "accuracy": round(best_acc, 4),
            "buy_precision": round(report["buy"]["precision"], 4),
            "buy_recall": round(report["buy"]["recall"], 4),
            "sell_precision": round(report["sell"]["precision"], 4),
            "sell_recall": round(report["sell"]["recall"], 4),
            "epochs": epochs, "train_loss_last": round(avg_loss, 4),
            "codes_used": ds["candle_count"], "total_market_codes": ds["total_codes"],
            "distributed": True, "num_workers": world_size,
        }
        ray.get(progress_actor.update.remote(
            task_id, status="completed", progress=100,
            message="训练完成", ended_at=time.time(), result=result,
        ))


# ─────────────────── LightGBM Remote Task ───────────────────


@ray.remote(num_cpus=4, num_gpus=0)
def train_lightgbm_remote(config: dict):
    """Train LightGBM as a Ray remote task (CPU-only)."""
    task_id = config["task_id"]
    progress_actor = ray.get_actor("progress_actor")

    try:
        ray.get(progress_actor.update.remote(
            task_id, status="loading", progress=2, message="加载数据...",
        ))
        candle_lists, total_codes = _load_candles_from_db(
            config["max_stocks"], config.get("min_days", 200)
        )
        if not candle_lists:
            ray.get(progress_actor.update.remote(
                task_id, status="failed", progress=0,
                message="数据不足", ended_at=time.time(),
            ))
            return

        ray.get(progress_actor.update.remote(
            task_id, status="training", progress=5,
            message=f"已加载 {len(candle_lists)} 只股票，开始 LightGBM 训练...",
            codes_used=len(candle_lists), total_codes=total_codes,
        ))

        import sys
        sys.path.insert(0, "/app/backend")
        from app.services.ai_strategy import train_lightgbm

        def _progress_cb(pct, msg):
            ray.get(progress_actor.update.remote(
                task_id, status="training", progress=min(pct, 95), message=msg,
            ))

        result = train_lightgbm(
            candle_lists,
            label_method=config.get("label_method", "zigzag"),
            pct_threshold=config.get("pct_threshold", 5.0),
            progress_cb=_progress_cb,
        )
        result["codes_used"] = len(candle_lists)
        result["total_market_codes"] = total_codes

        ray.get(progress_actor.update.remote(
            task_id, status="completed", progress=100,
            message="训练完成", ended_at=time.time(), result=result,
        ))
    except Exception as e:
        logger.exception("LightGBM training failed")
        ray.get(progress_actor.update.remote(
            task_id, status="failed", progress=0,
            message=f"训练失败: {e}", ended_at=time.time(),
        ))


# ─────────────────── RL-PPO Remote Task ───────────────────


@ray.remote(num_gpus=1)
def train_rl_remote(config: dict):
    """Train RL-PPO as a Ray remote task (1 GPU)."""
    task_id = config["task_id"]
    progress_actor = ray.get_actor("progress_actor")

    try:
        ray.get(progress_actor.update.remote(
            task_id, status="loading", progress=2, message="加载数据...",
        ))
        candle_lists, total_codes = _load_candles_from_db(
            config["max_stocks"], config.get("min_days", 200)
        )
        if not candle_lists:
            ray.get(progress_actor.update.remote(
                task_id, status="failed", progress=0,
                message="数据不足", ended_at=time.time(),
            ))
            return

        ray.get(progress_actor.update.remote(
            task_id, status="training", progress=5,
            message=f"已加载 {len(candle_lists)} 只股票，开始 RL-PPO 训练...",
            codes_used=len(candle_lists), total_codes=total_codes,
        ))

        import sys
        sys.path.insert(0, "/app/backend")
        from app.services.rl_strategy import train_rl

        epochs = config.get("epochs", 100)

        def _progress_cb(pct, msg):
            ray.get(progress_actor.update.remote(
                task_id, status="training", progress=min(pct, 95), message=msg,
            ))

        result = train_rl(
            candle_lists,
            total_timesteps=epochs * 2000,
            model_key="ai_rl",
            progress_cb=_progress_cb,
        )
        result["codes_used"] = len(candle_lists)
        result["total_market_codes"] = total_codes

        ray.get(progress_actor.update.remote(
            task_id, status="completed", progress=100,
            message="训练完成", ended_at=time.time(), result=result,
        ))
    except Exception as e:
        logger.exception("RL-PPO training failed")
        ray.get(progress_actor.update.remote(
            task_id, status="failed", progress=0,
            message=f"训练失败: {e}", ended_at=time.time(),
        ))


# ─────────────────── Unified Submit API ───────────────────


def submit_training(
    model_type: str,
    max_stocks: int = 200,
    epochs: int = 100,
    min_days: int = 200,
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    num_gpus: int = 1,
) -> dict:
    """Submit a training job to Ray. Returns immediately with task info.

    Args:
        model_type: "lightgbm", "transformer", "lstm", "cnn_lstm", "rl_ppo", or "all"
        num_gpus: 1 = single GPU, >1 = multi-GPU DDP (for transformer/lstm/cnn_lstm)
    """
    progress = get_progress_actor()

    # Check if same model is already training
    existing = ray.get(progress.get_all.remote())
    for task in existing:
        if task.get("model_type") == model_type and task.get("status") in ("loading", "training", "pending"):
            return {"error": f"{model_type} 已在训练中", "task": task}

    if model_type == "all":
        return _submit_all(max_stocks, epochs, min_days, label_method, pct_threshold, num_gpus)

    task_id = uuid.uuid4().hex[:8]
    t0 = time.time()

    init_state = dict(
        model_type=model_type, status="pending",
        progress=0, message="启动中...",
        gpu_id=-1, max_stocks=max_stocks, epochs=epochs,
        started_at=t0, ended_at=None, result=None,
        codes_used=0, total_codes=0,
    )
    ray.get(progress.update.remote(task_id, **init_state))

    config = dict(
        task_id=task_id, model_type=model_type,
        max_stocks=max_stocks, min_days=min_days,
        epochs=epochs, label_method=label_method,
        pct_threshold=pct_threshold,
    )

    if model_type == "lightgbm":
        ray.get(progress.update.remote(task_id, gpu_id=-1, message="启动中... (CPU)"))
        train_lightgbm_remote.remote(config)
    elif model_type == "rl_ppo":
        ray.get(progress.update.remote(task_id, gpu_id=1, message="启动中... (1 GPU)"))
        train_rl_remote.remote(config)
    elif model_type in ("transformer", "lstm", "cnn_lstm"):
        actual_gpus = min(num_gpus, NUM_GPUS)
        if actual_gpus > 1:
            # Multi-GPU DDP via TorchTrainer
            # Pre-build dataset ONCE to ensure all workers see identical data
            ray.get(progress.update.remote(
                task_id, gpu_id=actual_gpus,
                message=f"启动中... ({actual_gpus} GPU DDP) 准备数据...",
            ))
            ds = _build_dataset_common(config)
            if ds is None:
                ray.get(progress.update.remote(
                    task_id, status="failed", progress=0,
                    message="数据库中没有足够的K线数据", ended_at=time.time(),
                ))
                return {"task_id": task_id, "model_type": model_type,
                        "status": "failed", "message": "数据不足"}
            ds_ref = ray.put(ds)
            config["_dataset_ref"] = ds_ref

            from ray.train import RunConfig, ScalingConfig
            from ray.train.torch import TorchTrainer
            trainer = TorchTrainer(
                train_loop_per_worker=_torch_ddp_train_fn,
                train_loop_config=config,
                scaling_config=ScalingConfig(
                    num_workers=actual_gpus,
                    use_gpu=True,
                    resources_per_worker={"GPU": 1},
                ),
                run_config=RunConfig(
                    name=f"train_{model_type}_{task_id}",
                    storage_path="/tmp/ray_results",
                ),
            )
            import threading
            def _run_trainer():
                try:
                    trainer.fit()
                except Exception as e:
                    logger.exception("TorchTrainer DDP failed: %s", e)
                    ray.get(progress.update.remote(
                        task_id, status="failed", progress=0,
                        message=f"DDP 训练失败: {e}", ended_at=time.time(),
                    ))
            threading.Thread(target=_run_trainer, daemon=True).start()
        else:
            # Single GPU
            ray.get(progress.update.remote(task_id, gpu_id=1, message="启动中... (1 GPU)"))
            train_torch_remote.remote(config)
    else:
        return {"error": f"未知模型类型: {model_type}"}

    return {
        "task_id": task_id, "model_type": model_type,
        "status": "pending", "message": init_state["message"],
    }


def _submit_all(max_stocks, epochs, min_days, label_method, pct_threshold, num_gpus=1) -> dict:
    """Submit all 5 model types in parallel."""
    all_types = ["lightgbm", "transformer", "lstm", "cnn_lstm", "rl_ppo"]
    launched = []
    for mt in all_types:
        # For "all" mode each PyTorch model gets num_gpus (DDP if > 1)
        gpus = num_gpus if mt in ("transformer", "lstm", "cnn_lstm") else 1
        result = submit_training(
            model_type=mt, max_stocks=max_stocks, epochs=epochs,
            min_days=min_days, label_method=label_method,
            pct_threshold=pct_threshold, num_gpus=gpus,
        )
        launched.append(result)

    ok = sum(1 for r in launched if "task_id" in r)
    mode = f"DDP×{num_gpus}" if num_gpus > 1 else "单 GPU"
    return {
        "tasks": launched,
        "message": f"已启动 {ok}/{len(all_types)} 个训练任务 (Ray {mode})",
    }
