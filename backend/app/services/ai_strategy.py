"""AI Strategy — multi-model training & prediction for buy/sell/hold signals.

Supports two model backends:
  1. LightGBM  — fast, interpretable, works on CPU
  2. Transformer (temporal) — learns sequential patterns, benefits from GPU

Shared pipeline: labeler → features → model → signal → backtest
"""
from __future__ import annotations

import logging
import math
import pickle
import time as _time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal

import numpy as np

from ..schemas import Candle
from .labeler import label_candles, get_labeled_points

logger = logging.getLogger(__name__)

ModelType = Literal["lightgbm", "transformer", "lstm", "cnn_lstm", "ensemble", "rl_ppo"]
_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
_MODEL_DIR.mkdir(exist_ok=True)

FEATURE_NAMES = [
    # MA distance (multi-period)
    "ma5_dist", "ma10_dist", "ma20_dist", "ma60_dist",
    # Momentum
    "rsi_14", "rsi_6",
    "macd", "macd_signal", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    # Volatility
    "atr_pct", "bb_bandwidth", "bb_position",
    "volatility_20",
    # Volume
    "vol_ratio_5", "vol_ratio_10", "vol_trend",
    # Returns
    "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    # Price structure
    "price_position_20", "price_position_60",
    "body_ratio", "upper_shadow", "lower_shadow",
    # Trend
    "adx",
    "ma5_slope", "ma20_slope",
]

NUM_FEATURES = len(FEATURE_NAMES)  # 31
SEQ_LEN = 30  # lookback window for transformer


# ─────────────────────────── Feature Engineering ───────────────────────────

def _safe_div(a: float, b: float) -> float:
    return a / b if abs(b) > 1e-9 else 0.0


def extract_features(candles: list[Candle], idx: int) -> dict[str, float] | None:
    """Extract 31-dim feature vector at bar *idx*."""
    if idx < 60 or idx >= len(candles):
        return None
    c = candles[idx]
    closes = np.array([candles[j].close for j in range(idx - 59, idx + 1)])
    highs = np.array([candles[j].high for j in range(idx - 59, idx + 1)])
    lows = np.array([candles[j].low for j in range(idx - 59, idx + 1)])
    volumes = np.array([candles[j].volume for j in range(idx - 59, idx + 1)])
    opens = np.array([candles[j].open for j in range(idx - 59, idx + 1)])

    # Moving averages
    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes))

    # RSI
    def _rsi(period: int) -> float:
        diffs = np.diff(closes[-(period + 1):])
        g = np.mean(np.where(diffs > 0, diffs, 0))
        lo = np.mean(np.where(diffs < 0, -diffs, 0))
        rs = g / max(lo, 1e-9)
        return 100 - 100 / (1 + rs)

    rsi_14 = _rsi(14)
    rsi_6 = _rsi(6)

    # MACD
    ema12 = float(np.mean(closes[-12:]))  # simplified EMA as SMA
    ema26 = float(np.mean(closes[-26:]))
    macd_val = (ema12 - ema26) / c.close * 100
    macd_signal = float(np.mean(closes[-9:])) - ema26
    macd_signal = macd_signal / c.close * 100
    macd_hist = macd_val - macd_signal

    # KDJ
    low_9 = float(np.min(lows[-9:]))
    high_9 = float(np.max(highs[-9:]))
    rsv = _safe_div(c.close - low_9, high_9 - low_9) * 100
    kdj_k = rsv  # simplified
    kdj_d = float(np.mean([_safe_div(candles[idx - j].close - float(np.min(lows[-(9 + j):len(lows) - j] if j > 0 else lows[-9:])),
                                       float(np.max(highs[-(9 + j):len(highs) - j] if j > 0 else highs[-9:])) -
                                       float(np.min(lows[-(9 + j):len(lows) - j] if j > 0 else lows[-9:]))) * 100
                            for j in range(3)]))
    kdj_j = 3 * kdj_k - 2 * kdj_d

    # ATR
    trs = []
    for j in range(idx - 13, idx + 1):
        h, lo_p, pc = candles[j].high, candles[j].low, candles[j - 1].close
        trs.append(max(h - lo_p, abs(h - pc), abs(lo_p - pc)))
    atr = float(np.mean(trs))

    # Bollinger Bands
    bb_std = float(np.std(closes[-20:]))
    bb_upper = ma20 + 2 * bb_std
    bb_lower = ma20 - 2 * bb_std
    bb_bandwidth = _safe_div(bb_std, ma20) * 100
    bb_position = _safe_div(c.close - bb_lower, bb_upper - bb_lower)

    # Volatility
    rets_20 = np.diff(np.log(closes[-21:]))
    volatility_20 = float(np.std(rets_20)) * math.sqrt(250) * 100

    # Volume ratios
    avg_vol_5 = float(np.mean(volumes[-6:-1]))
    avg_vol_10 = float(np.mean(volumes[-11:-1]))
    vol_ratio_5 = _safe_div(float(c.volume), avg_vol_5)
    vol_ratio_10 = _safe_div(float(c.volume), avg_vol_10)
    # Volume trend: 5-day vol ma vs 10-day vol ma
    vol_trend = _safe_div(avg_vol_5, avg_vol_10)

    # Returns
    ret_1d = _safe_div(c.close - closes[-2], closes[-2]) * 100
    ret_3d = _safe_div(c.close - closes[-4], closes[-4]) * 100
    ret_5d = _safe_div(c.close - closes[-6], closes[-6]) * 100
    ret_10d = _safe_div(c.close - closes[-11], closes[-11]) * 100
    ret_20d = _safe_div(c.close - closes[-21], closes[-21]) * 100

    # Price position in range
    range_20 = float(np.max(highs[-20:]) - np.min(lows[-20:]))
    range_60 = float(np.max(highs) - np.min(lows))
    price_position_20 = _safe_div(c.close - float(np.min(lows[-20:])), range_20)
    price_position_60 = _safe_div(c.close - float(np.min(lows)), range_60)

    # Candle body
    body = abs(c.close - c.open)
    full_range = c.high - c.low
    body_ratio = _safe_div(body, full_range)
    upper_shadow = _safe_div(c.high - max(c.open, c.close), full_range)
    lower_shadow = _safe_div(min(c.open, c.close) - c.low, full_range)

    # ADX (simplified)
    dx_vals = []
    for j in range(idx - 13, idx + 1):
        h_diff = candles[j].high - candles[j - 1].high
        l_diff = candles[j - 1].low - candles[j].low
        plus_dm = h_diff if (h_diff > l_diff and h_diff > 0) else 0
        minus_dm = l_diff if (l_diff > h_diff and l_diff > 0) else 0
        tr_j = max(candles[j].high - candles[j].low,
                   abs(candles[j].high - candles[j - 1].close),
                   abs(candles[j].low - candles[j - 1].close))
        if tr_j > 0:
            dx_vals.append(abs(plus_dm - minus_dm) / tr_j * 100)
    adx = float(np.mean(dx_vals)) if dx_vals else 50.0

    # MA slopes (%)
    ma5_prev = float(np.mean(closes[-6:-1]))
    ma20_prev = float(np.mean(closes[-21:-1]))
    ma5_slope = _safe_div(ma5 - ma5_prev, ma5_prev) * 100
    ma20_slope = _safe_div(ma20 - ma20_prev, ma20_prev) * 100

    return {
        "ma5_dist": _safe_div(c.close - ma5, ma5) * 100,
        "ma10_dist": _safe_div(c.close - ma10, ma10) * 100,
        "ma20_dist": _safe_div(c.close - ma20, ma20) * 100,
        "ma60_dist": _safe_div(c.close - ma60, ma60) * 100,
        "rsi_14": rsi_14,
        "rsi_6": rsi_6,
        "macd": macd_val,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "kdj_k": kdj_k,
        "kdj_d": kdj_d,
        "kdj_j": kdj_j,
        "atr_pct": _safe_div(atr, c.close) * 100,
        "bb_bandwidth": bb_bandwidth,
        "bb_position": bb_position,
        "volatility_20": volatility_20,
        "vol_ratio_5": vol_ratio_5,
        "vol_ratio_10": vol_ratio_10,
        "vol_trend": vol_trend,
        "ret_1d": ret_1d,
        "ret_3d": ret_3d,
        "ret_5d": ret_5d,
        "ret_10d": ret_10d,
        "ret_20d": ret_20d,
        "price_position_20": price_position_20,
        "price_position_60": price_position_60,
        "body_ratio": body_ratio,
        "upper_shadow": upper_shadow,
        "lower_shadow": lower_shadow,
        "adx": adx,
        "ma5_slope": ma5_slope,
        "ma20_slope": ma20_slope,
    }


# ─────────────────────────── Dataset Building ───────────────────────────

def build_dataset(
    candles: list[Candle],
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) dataset. y: 0=hold, 1=buy, 2=sell."""
    labels = label_candles(candles, method=label_method, pct_threshold=pct_threshold)

    X_list: list[list[float]] = []
    y_list: list[int] = []

    for i in range(60, len(candles)):
        feat = extract_features(candles, i)
        if feat is None:
            continue
        X_list.append([feat[k] for k in FEATURE_NAMES])
        y_list.append(labels[i])

    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int64)


def build_sequence_dataset(
    candles: list[Candle],
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    seq_len: int = SEQ_LEN,
) -> tuple[np.ndarray, np.ndarray]:
    """Build sequential dataset for Transformer. Shape: (N, seq_len, features)."""
    labels = label_candles(candles, method=label_method, pct_threshold=pct_threshold)

    # First build all features
    all_feats: list[list[float]] = []
    valid_indices: list[int] = []
    for i in range(60, len(candles)):
        feat = extract_features(candles, i)
        if feat is not None:
            all_feats.append([feat[k] for k in FEATURE_NAMES])
            valid_indices.append(i)

    if len(all_feats) < seq_len + 1:
        return np.array([]), np.array([])

    feats_arr = np.array(all_feats, dtype=np.float32)

    X_seqs: list[np.ndarray] = []
    y_seqs: list[int] = []

    for j in range(seq_len, len(feats_arr)):
        X_seqs.append(feats_arr[j - seq_len:j])
        y_seqs.append(labels[valid_indices[j]])

    return np.array(X_seqs, dtype=np.float32), np.array(y_seqs, dtype=np.int64)


# ─────────────────────────── LightGBM Model ───────────────────────────

def train_lightgbm(
    candle_lists: list[list[Candle]],
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    model_key: str = "ai_lgb",
    progress_cb=None,
) -> dict:
    """Train LightGBM multi-class classifier (buy/sell/hold)."""
    import lightgbm as lgb
    from sklearn.metrics import accuracy_score, classification_report

    t0 = _time.time()

    all_X: list[np.ndarray] = []
    all_y: list[np.ndarray] = []
    for candles in candle_lists:
        if len(candles) < 100:
            continue
        X, y = build_dataset(candles, label_method, pct_threshold)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return {"error": "insufficient data"}

    X = np.vstack(all_X)
    y = np.concatenate(all_y)

    class_counts = {int(c): int(np.sum(y == c)) for c in [0, 1, 2]}
    total = len(y)
    logger.info("Dataset: %d samples, class distribution: %s", total, class_counts)

    if class_counts.get(1, 0) < 5 or class_counts.get(2, 0) < 5:
        return {"error": "too few buy/sell samples", "class_counts": class_counts}

    if progress_cb:
        progress_cb(10, f"LightGBM: 数据集 {total} 样本，开始训练...")

    # Walk-forward split: last 20% as test
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # Class weights for imbalance
    weights = np.ones(len(y_train))
    for cls in [0, 1, 2]:
        mask = y_train == cls
        if mask.sum() > 0:
            weights[mask] = total / (3 * mask.sum())

    dtrain = lgb.Dataset(X_train, label=y_train, weight=weights, feature_name=FEATURE_NAMES)
    dtest = lgb.Dataset(X_test, label=y_test, reference=dtrain)

    params = {
        "objective": "multiclass",
        "num_class": 3,
        "metric": "multi_logloss",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "max_depth": 8,
        "min_child_samples": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "verbose": -1,
    }

    def _lgb_progress(env):
        if progress_cb and env.iteration % 30 == 0:
            pct = 10 + int(env.iteration / 300 * 70)
            progress_cb(min(pct, 80), f"LightGBM: round {env.iteration}/300")

    cbs = [lgb.early_stopping(30, verbose=False)]
    if progress_cb:
        cbs.append(_lgb_progress)

    model = lgb.train(
        params, dtrain,
        num_boost_round=300,
        valid_sets=[dtest],
        callbacks=cbs,
    )

    if progress_cb:
        progress_cb(85, "LightGBM: 评估模型...")

    # Evaluate
    y_pred_prob = model.predict(X_test)  # (N, 3)
    y_pred = np.argmax(y_pred_prob, axis=1)
    acc = accuracy_score(y_test, y_pred)

    report = classification_report(y_test, y_pred, target_names=["hold", "buy", "sell"],
                                   output_dict=True, zero_division=0)

    importance = dict(zip(FEATURE_NAMES, model.feature_importance(importance_type="gain").tolist()))

    # Save
    model_path = _MODEL_DIR / f"{model_key}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    elapsed = _time.time() - t0
    return {
        "model": "lightgbm",
        "samples": int(total),
        "class_counts": class_counts,
        "accuracy": round(acc, 4),
        "buy_precision": round(report["buy"]["precision"], 4),
        "buy_recall": round(report["buy"]["recall"], 4),
        "sell_precision": round(report["sell"]["precision"], 4),
        "sell_recall": round(report["sell"]["recall"], 4),
        "feature_importance": {k: round(v, 1) for k, v in
                               sorted(importance.items(), key=lambda x: -x[1])[:15]},
        "elapsed_sec": round(elapsed, 1),
    }


def predict_lightgbm(candles: list[Candle], model_key: str = "ai_lgb") -> dict | None:
    """Predict buy/sell/hold probabilities for the latest bar."""
    model_path = _MODEL_DIR / f"{model_key}.pkl"
    if not model_path.exists():
        return None

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    feat = extract_features(candles, len(candles) - 1)
    if feat is None:
        return None

    X = np.array([[feat[k] for k in FEATURE_NAMES]])
    probs = model.predict(X)[0]  # [hold, buy, sell]

    return {
        "hold_prob": round(float(probs[0]), 4),
        "buy_prob": round(float(probs[1]), 4),
        "sell_prob": round(float(probs[2]), 4),
        "action": ["hold", "buy", "sell"][int(np.argmax(probs))],
        "confidence": round(float(np.max(probs)) * 100, 1),
    }


# ─────────────────────────── Transformer Model ───────────────────────────

def _get_device():
    """Get best available device."""
    import torch
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _build_transformer():
    """Build temporal transformer for classification."""
    import torch
    import torch.nn as nn

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model: int, max_len: int = 200):
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(position * div_term)
            if d_model > 1:
                pe[:, 1::2] = torch.cos(position * div_term[:d_model // 2])
            self.register_buffer("pe", pe.unsqueeze(0))  # (1, max_len, d_model)

        def forward(self, x):
            return x + self.pe[:, :x.size(1)]

    class TradingTransformer(nn.Module):
        def __init__(
            self,
            n_features: int = NUM_FEATURES,
            d_model: int = 128,
            nhead: int = 4,
            num_layers: int = 3,
            dim_feedforward: int = 256,
            dropout: float = 0.1,
            n_classes: int = 3,
        ):
            super().__init__()
            self.input_proj = nn.Linear(n_features, d_model)
            self.pos_enc = PositionalEncoding(d_model)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead,
                dim_feedforward=dim_feedforward,
                dropout=dropout, batch_first=True,
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.norm = nn.LayerNorm(d_model)
            self.classifier = nn.Sequential(
                nn.Linear(d_model, 64),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(64, n_classes),
            )

        def forward(self, x):
            # x: (batch, seq_len, features)
            x = self.input_proj(x)
            x = self.pos_enc(x)
            x = self.transformer(x)
            # Use the last time step for classification
            x = self.norm(x[:, -1, :])
            return self.classifier(x)

    return TradingTransformer


def train_transformer(
    candle_lists: list[list[Candle]],
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    epochs: int = 50,
    batch_size: int = 64,
    lr: float = 1e-3,
    model_key: str = "ai_trf",
    progress_cb=None,
) -> dict:
    """Train temporal transformer on GPU/CPU."""
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    t0 = _time.time()
    device = _get_device()
    logger.info("Training transformer on %s", device)

    # Build dataset
    all_X: list[np.ndarray] = []
    all_y: list[np.ndarray] = []
    for candles in candle_lists:
        if len(candles) < 120:
            continue
        X, y = build_sequence_dataset(candles, label_method, pct_threshold)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return {"error": "insufficient data"}

    X = np.concatenate(all_X)
    y = np.concatenate(all_y)

    class_counts = {int(c): int(np.sum(y == c)) for c in [0, 1, 2]}
    total = len(y)
    logger.info("Transformer dataset: %d samples, classes: %s", total, class_counts)

    if class_counts.get(1, 0) < 5 or class_counts.get(2, 0) < 5:
        return {"error": "too few buy/sell samples", "class_counts": class_counts}

    if progress_cb:
        progress_cb(10, f"Transformer: {total} 样本，准备数据...")

    # Normalize features
    mean = X.reshape(-1, NUM_FEATURES).mean(axis=0)
    std = X.reshape(-1, NUM_FEATURES).std(axis=0) + 1e-8
    X = (X - mean) / std

    # Save normalization params
    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    with open(norm_path, "wb") as f:
        pickle.dump({"mean": mean, "std": std}, f)

    # Walk-forward split
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # Class weights
    class_weights = torch.ones(3, device=device)
    for cls in [0, 1, 2]:
        cnt = (y_train == cls).sum()
        if cnt > 0:
            class_weights[cls] = total / (3 * cnt)

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test))
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    test_dl = DataLoader(test_ds, batch_size=batch_size)

    # Build model
    TransformerCls = _build_transformer()
    model = TransformerCls().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    best_acc = 0.0
    best_state = None
    train_losses = []

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        for xb, yb in train_dl:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item() * len(xb)
        scheduler.step()

        avg_loss = epoch_loss / len(train_ds)
        train_losses.append(round(avg_loss, 4))

        # Evaluate
        if progress_cb:
            pct = 15 + int((epoch + 1) / epochs * 70)
            progress_cb(min(pct, 85), f"Transformer: Epoch {epoch+1}/{epochs} loss={avg_loss:.4f}")

        if (epoch + 1) % 5 == 0 or epoch == epochs - 1:
            model.eval()
            correct = 0
            total_test = 0
            with torch.no_grad():
                for xb, yb in test_dl:
                    xb, yb = xb.to(device), yb.to(device)
                    logits = model(xb)
                    preds = logits.argmax(dim=1)
                    correct += (preds == yb).sum().item()
                    total_test += len(yb)
            acc = correct / total_test if total_test > 0 else 0
            logger.info("Epoch %d/%d loss=%.4f test_acc=%.4f", epoch + 1, epochs, avg_loss, acc)
            if progress_cb:
                progress_cb(min(15 + int((epoch + 1) / epochs * 70), 85),
                            f"Transformer: Epoch {epoch+1}/{epochs} acc={acc:.4f}")
            if acc > best_acc:
                best_acc = acc
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    # Save best model
    if best_state is not None:
        model_path = _MODEL_DIR / f"{model_key}.pt"
        torch.save(best_state, model_path)

    # Final detailed evaluation
    model.load_state_dict(best_state or model.state_dict())
    model.eval()
    all_preds = []
    all_true = []
    with torch.no_grad():
        for xb, yb in test_dl:
            xb = xb.to(device)
            logits = model(xb)
            all_preds.extend(logits.argmax(dim=1).cpu().numpy().tolist())
            all_true.extend(yb.numpy().tolist())

    from sklearn.metrics import classification_report
    report = classification_report(
        all_true, all_preds,
        target_names=["hold", "buy", "sell"],
        output_dict=True, zero_division=0,
    )

    elapsed = _time.time() - t0
    return {
        "model": "transformer",
        "device": str(device),
        "samples": int(len(X)),
        "class_counts": class_counts,
        "accuracy": round(best_acc, 4),
        "buy_precision": round(report["buy"]["precision"], 4),
        "buy_recall": round(report["buy"]["recall"], 4),
        "sell_precision": round(report["sell"]["precision"], 4),
        "sell_recall": round(report["sell"]["recall"], 4),
        "epochs": epochs,
        "train_loss_last": train_losses[-1] if train_losses else 0,
        "elapsed_sec": round(elapsed, 1),
    }


def predict_transformer(
    candles: list[Candle],
    model_key: str = "ai_trf",
    seq_len: int = SEQ_LEN,
) -> dict | None:
    """Predict buy/sell/hold with transformer for the latest bar."""
    import torch

    model_path = _MODEL_DIR / f"{model_key}.pt"
    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    if not model_path.exists() or not norm_path.exists():
        return None

    device = _get_device()

    # Build feature sequence for the last seq_len bars
    feats: list[list[float]] = []
    for i in range(len(candles) - seq_len, len(candles)):
        feat = extract_features(candles, i)
        if feat is None:
            return None
        feats.append([feat[k] for k in FEATURE_NAMES])

    X = np.array([feats], dtype=np.float32)  # (1, seq_len, features)

    # Normalize
    with open(norm_path, "rb") as f:
        norm = pickle.load(f)
    X = (X - norm["mean"]) / norm["std"]

    # Load model
    TransformerCls = _build_transformer()
    model = TransformerCls().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.eval()

    with torch.no_grad():
        logits = model(torch.from_numpy(X).to(device))
        probs = torch.softmax(logits, dim=1)[0].cpu().numpy()

    return {
        "hold_prob": round(float(probs[0]), 4),
        "buy_prob": round(float(probs[1]), 4),
        "sell_prob": round(float(probs[2]), 4),
        "action": ["hold", "buy", "sell"][int(np.argmax(probs))],
        "confidence": round(float(np.max(probs)) * 100, 1),
    }


# ─────────────────────────── LSTM Model ───────────────────────────

def _build_lstm():
    """Build LSTM model for sequence classification."""
    import torch
    import torch.nn as nn

    class TradingLSTM(nn.Module):
        def __init__(
            self,
            n_features: int = NUM_FEATURES,
            hidden_size: int = 128,
            num_layers: int = 2,
            dropout: float = 0.2,
            n_classes: int = 3,
        ):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=n_features,
                hidden_size=hidden_size,
                num_layers=num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0,
                bidirectional=True,
            )
            self.norm = nn.LayerNorm(hidden_size * 2)
            self.classifier = nn.Sequential(
                nn.Linear(hidden_size * 2, 64),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(64, n_classes),
            )

        def forward(self, x):
            # x: (batch, seq_len, features)
            out, _ = self.lstm(x)
            # Use the last time step output
            out = self.norm(out[:, -1, :])
            return self.classifier(out)

    return TradingLSTM


def train_lstm(
    candle_lists: list[list[Candle]],
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    epochs: int = 50,
    batch_size: int = 64,
    lr: float = 1e-3,
    model_key: str = "ai_lstm",
    progress_cb=None,
) -> dict:
    """Train bidirectional LSTM on GPU/CPU."""
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    t0 = _time.time()
    device = _get_device()
    logger.info("Training LSTM on %s", device)

    all_X, all_y = [], []
    for candles in candle_lists:
        if len(candles) < 120:
            continue
        X, y = build_sequence_dataset(candles, label_method, pct_threshold)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return {"error": "insufficient data"}

    X = np.concatenate(all_X)
    y = np.concatenate(all_y)

    class_counts = {int(c): int(np.sum(y == c)) for c in [0, 1, 2]}
    total = len(y)

    if class_counts.get(1, 0) < 5 or class_counts.get(2, 0) < 5:
        return {"error": "too few buy/sell samples", "class_counts": class_counts}

    if progress_cb:
        progress_cb(10, f"LSTM: {total} 样本，准备数据...")

    # Normalize
    mean = X.reshape(-1, NUM_FEATURES).mean(axis=0)
    std = X.reshape(-1, NUM_FEATURES).std(axis=0) + 1e-8
    X = (X - mean) / std

    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    with open(norm_path, "wb") as f:
        pickle.dump({"mean": mean, "std": std}, f)

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    class_weights = torch.ones(3, device=device)
    for cls in [0, 1, 2]:
        cnt = (y_train == cls).sum()
        if cnt > 0:
            class_weights[cls] = total / (3 * cnt)

    train_dl = DataLoader(
        TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)),
        batch_size=batch_size, shuffle=True,
    )
    test_dl = DataLoader(
        TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test)),
        batch_size=batch_size,
    )

    LSTMCls = _build_lstm()
    model = LSTMCls().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    best_acc = 0.0
    best_state = None
    train_losses = []

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        for xb, yb in train_dl:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item() * len(xb)
        scheduler.step()

        avg_loss = epoch_loss / len(X_train)
        train_losses.append(round(avg_loss, 4))

        if progress_cb:
            pct = 15 + int((epoch + 1) / epochs * 70)
            progress_cb(min(pct, 85), f"LSTM: Epoch {epoch+1}/{epochs} loss={avg_loss:.4f}")

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
            logger.info("LSTM Epoch %d/%d loss=%.4f acc=%.4f", epoch + 1, epochs, avg_loss, acc)
            if progress_cb:
                progress_cb(min(15 + int((epoch + 1) / epochs * 70), 85),
                            f"LSTM: Epoch {epoch+1}/{epochs} acc={acc:.4f}")
            if acc > best_acc:
                best_acc = acc
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        torch.save(best_state, _MODEL_DIR / f"{model_key}.pt")

    model.load_state_dict(best_state or model.state_dict())
    model.eval()
    all_preds, all_true = [], []
    with torch.no_grad():
        for xb, yb in test_dl:
            all_preds.extend(model(xb.to(device)).argmax(dim=1).cpu().numpy().tolist())
            all_true.extend(yb.numpy().tolist())

    from sklearn.metrics import classification_report
    report = classification_report(all_true, all_preds, target_names=["hold", "buy", "sell"],
                                   output_dict=True, zero_division=0)

    elapsed = _time.time() - t0
    return {
        "model": "lstm",
        "device": str(device),
        "samples": int(len(X)),
        "class_counts": class_counts,
        "accuracy": round(best_acc, 4),
        "buy_precision": round(report["buy"]["precision"], 4),
        "buy_recall": round(report["buy"]["recall"], 4),
        "sell_precision": round(report["sell"]["precision"], 4),
        "sell_recall": round(report["sell"]["recall"], 4),
        "epochs": epochs,
        "train_loss_last": train_losses[-1] if train_losses else 0,
        "elapsed_sec": round(elapsed, 1),
    }


def predict_lstm(
    candles: list[Candle],
    model_key: str = "ai_lstm",
    seq_len: int = SEQ_LEN,
) -> dict | None:
    """Predict with trained LSTM model."""
    import torch

    model_path = _MODEL_DIR / f"{model_key}.pt"
    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    if not model_path.exists() or not norm_path.exists():
        return None

    device = _get_device()

    feats = []
    for i in range(len(candles) - seq_len, len(candles)):
        feat = extract_features(candles, i)
        if feat is None:
            return None
        feats.append([feat[k] for k in FEATURE_NAMES])

    X = np.array([feats], dtype=np.float32)

    with open(norm_path, "rb") as f:
        norm = pickle.load(f)
    X = (X - norm["mean"]) / norm["std"]

    LSTMCls = _build_lstm()
    model = LSTMCls().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.eval()

    with torch.no_grad():
        logits = model(torch.from_numpy(X).to(device))
        probs = torch.softmax(logits, dim=1)[0].cpu().numpy()

    return {
        "hold_prob": round(float(probs[0]), 4),
        "buy_prob": round(float(probs[1]), 4),
        "sell_prob": round(float(probs[2]), 4),
        "action": ["hold", "buy", "sell"][int(np.argmax(probs))],
        "confidence": round(float(np.max(probs)) * 100, 1),
    }


# ─────────────────────────── CNN-LSTM Model ───────────────────────────

def _build_cnn_lstm():
    """Build CNN-LSTM hybrid: CNN extracts local patterns, LSTM captures temporal dependencies."""
    import torch
    import torch.nn as nn

    class TradingCNNLSTM(nn.Module):
        def __init__(
            self,
            n_features: int = NUM_FEATURES,
            cnn_channels: int = 64,
            lstm_hidden: int = 128,
            num_lstm_layers: int = 2,
            dropout: float = 0.2,
            n_classes: int = 3,
        ):
            super().__init__()
            # CNN: extract local patterns from feature windows
            self.cnn = nn.Sequential(
                # (batch, 1, seq_len, features) -> (batch, cnn_channels, seq_len, 1)
                nn.Conv2d(1, cnn_channels, kernel_size=(3, n_features), padding=(1, 0)),
                nn.BatchNorm2d(cnn_channels),
                nn.ReLU(),
                nn.Conv2d(cnn_channels, cnn_channels, kernel_size=(3, 1), padding=(1, 0)),
                nn.BatchNorm2d(cnn_channels),
                nn.ReLU(),
            )
            # LSTM: process CNN feature sequence
            self.lstm = nn.LSTM(
                input_size=cnn_channels,
                hidden_size=lstm_hidden,
                num_layers=num_lstm_layers,
                batch_first=True,
                dropout=dropout if num_lstm_layers > 1 else 0,
                bidirectional=True,
            )
            # Attention pooling
            self.attention = nn.Sequential(
                nn.Linear(lstm_hidden * 2, 1),
                nn.Softmax(dim=1),
            )
            self.classifier = nn.Sequential(
                nn.LayerNorm(lstm_hidden * 2),
                nn.Linear(lstm_hidden * 2, 64),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(64, n_classes),
            )

        def forward(self, x):
            # x: (batch, seq_len, features)
            batch_size = x.size(0)
            # Reshape for CNN: (batch, 1, seq_len, features)
            cnn_in = x.unsqueeze(1)
            cnn_out = self.cnn(cnn_in)  # (batch, channels, seq_len, 1)
            cnn_out = cnn_out.squeeze(-1).permute(0, 2, 1)  # (batch, seq_len, channels)

            lstm_out, _ = self.lstm(cnn_out)  # (batch, seq_len, hidden*2)

            # Attention-weighted pooling
            attn_weights = self.attention(lstm_out)  # (batch, seq_len, 1)
            context = (lstm_out * attn_weights).sum(dim=1)  # (batch, hidden*2)

            return self.classifier(context)

    return TradingCNNLSTM


def train_cnn_lstm(
    candle_lists: list[list[Candle]],
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    epochs: int = 50,
    batch_size: int = 64,
    lr: float = 1e-3,
    model_key: str = "ai_cnn_lstm",
    progress_cb=None,
) -> dict:
    """Train CNN-LSTM hybrid on GPU/CPU."""
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    t0 = _time.time()
    device = _get_device()
    logger.info("Training CNN-LSTM on %s", device)

    all_X, all_y = [], []
    for candles in candle_lists:
        if len(candles) < 120:
            continue
        X, y = build_sequence_dataset(candles, label_method, pct_threshold)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return {"error": "insufficient data"}

    X = np.concatenate(all_X)
    y = np.concatenate(all_y)

    class_counts = {int(c): int(np.sum(y == c)) for c in [0, 1, 2]}
    total = len(y)

    if class_counts.get(1, 0) < 5 or class_counts.get(2, 0) < 5:
        return {"error": "too few buy/sell samples", "class_counts": class_counts}

    if progress_cb:
        progress_cb(10, f"CNN-LSTM: {total} 样本，准备数据...")

    # Normalize
    mean = X.reshape(-1, NUM_FEATURES).mean(axis=0)
    std = X.reshape(-1, NUM_FEATURES).std(axis=0) + 1e-8
    X = (X - mean) / std

    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    with open(norm_path, "wb") as f:
        pickle.dump({"mean": mean, "std": std}, f)

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    class_weights = torch.ones(3, device=device)
    for cls in [0, 1, 2]:
        cnt = (y_train == cls).sum()
        if cnt > 0:
            class_weights[cls] = total / (3 * cnt)

    train_dl = DataLoader(
        TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)),
        batch_size=batch_size, shuffle=True,
    )
    test_dl = DataLoader(
        TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test)),
        batch_size=batch_size,
    )

    CNNLSTMCls = _build_cnn_lstm()
    model = CNNLSTMCls().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    best_acc = 0.0
    best_state = None
    train_losses = []

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        for xb, yb in train_dl:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item() * len(xb)
        scheduler.step()

        avg_loss = epoch_loss / len(X_train)
        train_losses.append(round(avg_loss, 4))

        if progress_cb:
            pct = 15 + int((epoch + 1) / epochs * 70)
            progress_cb(min(pct, 85), f"CNN-LSTM: Epoch {epoch+1}/{epochs} loss={avg_loss:.4f}")

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
            logger.info("CNN-LSTM Epoch %d/%d loss=%.4f acc=%.4f", epoch + 1, epochs, avg_loss, acc)
            if progress_cb:
                progress_cb(min(15 + int((epoch + 1) / epochs * 70), 85),
                            f"CNN-LSTM: Epoch {epoch+1}/{epochs} acc={acc:.4f}")
            if acc > best_acc:
                best_acc = acc
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        torch.save(best_state, _MODEL_DIR / f"{model_key}.pt")

    model.load_state_dict(best_state or model.state_dict())
    model.eval()
    all_preds, all_true = [], []
    with torch.no_grad():
        for xb, yb in test_dl:
            all_preds.extend(model(xb.to(device)).argmax(dim=1).cpu().numpy().tolist())
            all_true.extend(yb.numpy().tolist())

    from sklearn.metrics import classification_report
    report = classification_report(all_true, all_preds, target_names=["hold", "buy", "sell"],
                                   output_dict=True, zero_division=0)

    elapsed = _time.time() - t0
    return {
        "model": "cnn_lstm",
        "device": str(device),
        "samples": int(len(X)),
        "class_counts": class_counts,
        "accuracy": round(best_acc, 4),
        "buy_precision": round(report["buy"]["precision"], 4),
        "buy_recall": round(report["buy"]["recall"], 4),
        "sell_precision": round(report["sell"]["precision"], 4),
        "sell_recall": round(report["sell"]["recall"], 4),
        "epochs": epochs,
        "train_loss_last": train_losses[-1] if train_losses else 0,
        "elapsed_sec": round(elapsed, 1),
    }


def predict_cnn_lstm(
    candles: list[Candle],
    model_key: str = "ai_cnn_lstm",
    seq_len: int = SEQ_LEN,
) -> dict | None:
    """Predict with trained CNN-LSTM model."""
    import torch

    model_path = _MODEL_DIR / f"{model_key}.pt"
    norm_path = _MODEL_DIR / f"{model_key}_norm.pkl"
    if not model_path.exists() or not norm_path.exists():
        return None

    device = _get_device()

    feats = []
    for i in range(len(candles) - seq_len, len(candles)):
        feat = extract_features(candles, i)
        if feat is None:
            return None
        feats.append([feat[k] for k in FEATURE_NAMES])

    X = np.array([feats], dtype=np.float32)

    with open(norm_path, "rb") as f:
        norm = pickle.load(f)
    X = (X - norm["mean"]) / norm["std"]

    CNNLSTMCls = _build_cnn_lstm()
    model = CNNLSTMCls().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.eval()

    with torch.no_grad():
        logits = model(torch.from_numpy(X).to(device))
        probs = torch.softmax(logits, dim=1)[0].cpu().numpy()

    return {
        "hold_prob": round(float(probs[0]), 4),
        "buy_prob": round(float(probs[1]), 4),
        "sell_prob": round(float(probs[2]), 4),
        "action": ["hold", "buy", "sell"][int(np.argmax(probs))],
        "confidence": round(float(np.max(probs)) * 100, 1),
    }


# ─────────────────────────── Unified API ───────────────────────────

def train_model(
    candle_lists: list[list[Candle]],
    model_type: ModelType = "lightgbm",
    label_method: str = "zigzag",
    pct_threshold: float = 5.0,
    epochs: int = 50,
    model_key: str | None = None,
    progress_cb=None,
) -> dict:
    """Train either LightGBM or Transformer model."""
    _default_keys = {"lightgbm": "ai_lgb", "transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm", "rl_ppo": "ai_rl"}
    key = model_key or _default_keys.get(model_type, f"ai_{model_type}")
    if model_type == "rl_ppo":
        from .rl_strategy import train_rl
        return train_rl(candle_lists, total_timesteps=epochs * 2000, model_key=key, progress_cb=progress_cb)
    elif model_type == "transformer":
        return train_transformer(
            candle_lists, label_method, pct_threshold,
            epochs=epochs, model_key=key, progress_cb=progress_cb,
        )
    elif model_type == "lstm":
        return train_lstm(
            candle_lists, label_method, pct_threshold,
            epochs=epochs, model_key=key, progress_cb=progress_cb,
        )
    elif model_type == "cnn_lstm":
        return train_cnn_lstm(
            candle_lists, label_method, pct_threshold,
            epochs=epochs, model_key=key, progress_cb=progress_cb,
        )
    else:
        return train_lightgbm(
            candle_lists, label_method, pct_threshold, model_key=key,
            progress_cb=progress_cb,
        )


def predict(
    candles: list[Candle],
    model_type: ModelType = "lightgbm",
    model_key: str | None = None,
) -> dict | None:
    """Predict with trained model."""
    _default_keys = {"lightgbm": "ai_lgb", "transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm", "rl_ppo": "ai_rl"}
    key = model_key or _default_keys.get(model_type, f"ai_{model_type}")
    if model_type == "rl_ppo":
        from .rl_strategy import predict_rl
        return predict_rl(candles, model_key=key)
    elif model_type == "transformer":
        return predict_transformer(candles, model_key=key)
    elif model_type == "lstm":
        return predict_lstm(candles, model_key=key)
    elif model_type == "cnn_lstm":
        return predict_cnn_lstm(candles, model_key=key)
    else:
        return predict_lightgbm(candles, model_key=key)


def predict_ensemble(candles: list[Candle]) -> dict | None:
    """Ensemble prediction: combine all trained models with weighted voting.

    Strategy:
      - Collect predictions from all trained models
      - Weight by model strength: Transformer (precision) > LSTM (recall) > CNN-LSTM > LightGBM
      - Require agreement: at least 2 models must agree on buy/sell
      - Confidence = weighted average confidence
    """
    model_configs = [
        ("transformer", "ai_trf", 1.5),   # highest buy precision → trust buy signals
        ("lstm", "ai_lstm", 1.3),          # highest sell recall → trust sell signals
        ("cnn_lstm", "ai_cnn_lstm", 1.1),  # good sell precision
        ("lightgbm", "ai_lgb", 1.0),       # baseline, fast
        ("rl_ppo", "ai_rl", 1.2),          # RL agent, self-learned strategy
    ]

    preds: list[tuple[dict, float]] = []
    model_details: list[dict] = []
    for mtype, mkey, weight in model_configs:
        p = predict(candles, model_type=mtype, model_key=mkey)  # type: ignore
        if p is not None:
            preds.append((p, weight))
            model_details.append({
                "model": mtype,
                "action": p["action"],
                "buy_prob": p["buy_prob"],
                "sell_prob": p["sell_prob"],
                "confidence": p["confidence"],
            })

    if not preds:
        return None

    # Weighted probability averaging
    total_weight = sum(w for _, w in preds)
    avg_hold = sum(p["hold_prob"] * w for p, w in preds) / total_weight
    avg_buy = sum(p["buy_prob"] * w for p, w in preds) / total_weight
    avg_sell = sum(p["sell_prob"] * w for p, w in preds) / total_weight

    # Vote counting
    buy_votes = sum(1 for p, _ in preds if p["action"] == "buy")
    sell_votes = sum(1 for p, _ in preds if p["action"] == "sell")
    n_models = len(preds)

    # Decision: need majority (≥2 models) to act, otherwise hold
    if buy_votes >= 2 and avg_buy > avg_sell:
        action = "buy"
        confidence = round(avg_buy * 100, 1)
    elif sell_votes >= 2 and avg_sell > avg_buy:
        action = "sell"
        confidence = round(avg_sell * 100, 1)
    else:
        action = "hold"
        confidence = 0

    # Boost confidence if unanimous
    if buy_votes == n_models or sell_votes == n_models:
        confidence = min(confidence * 1.2, 100)

    return {
        "hold_prob": round(avg_hold, 4),
        "buy_prob": round(avg_buy, 4),
        "sell_prob": round(avg_sell, 4),
        "action": action,
        "confidence": round(confidence, 1),
        "models_used": n_models,
        "buy_votes": buy_votes,
        "sell_votes": sell_votes,
        "model_details": model_details,
    }


def generate_ai_signal(
    candles: list[Candle],
    model_type: ModelType = "lightgbm",
    model_key: str | None = None,
    buy_threshold: float = 0.4,
    sell_threshold: float = 0.4,
) -> dict:
    """Generate trading signal from AI prediction.

    Combines model prediction with technical analysis for entry/stop/target.
    """
    if model_type == "ensemble":
        pred = predict_ensemble(candles)
    else:
        _default_keys = {"lightgbm": "ai_lgb", "transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm", "rl_ppo": "ai_rl"}
        key = model_key or _default_keys.get(model_type, f"ai_{model_type}")
        pred = predict(candles, model_type, key)
    if pred is None:
        return {"error": f"{model_type} model not trained yet"}

    c = candles[-1]
    price = c.close

    # Get ATR for stop/target calculation
    atr = 0.0
    if len(candles) >= 15:
        trs = []
        for j in range(len(candles) - 14, len(candles)):
            h, lo, pc = candles[j].high, candles[j].low, candles[j - 1].close
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr = float(np.mean(trs))

    # Support / resistance from recent prices
    recent = candles[-60:] if len(candles) >= 60 else candles
    recent_lows = sorted([c.low for c in recent])
    recent_highs = sorted([c.high for c in recent], reverse=True)
    nearest_support = float(np.percentile(recent_lows, 10))
    nearest_resistance = float(np.percentile(recent_highs, 10))

    # Trend
    if len(candles) >= 20:
        ma20 = float(np.mean([c.close for c in candles[-20:]]))
        trend = "up" if price > ma20 else "down"
    else:
        trend = "neutral"

    buy_prob = pred["buy_prob"]
    sell_prob = pred["sell_prob"]

    # For ensemble mode, respect the voting result directly
    if model_type == "ensemble":
        ensemble_action = pred.get("action", "hold")
        if ensemble_action == "buy":
            buy_prob = max(buy_prob, buy_threshold)  # ensure threshold is met
        elif ensemble_action == "sell":
            sell_prob = max(sell_prob, sell_threshold)
        else:
            # Hold: suppress both signals regardless of probability
            buy_prob = 0.0
            sell_prob = 0.0

    if buy_prob >= buy_threshold and buy_prob > sell_prob:
        action = "buy"
        entry_price = round(price, 2)
        stop_loss = round(max(price - 2 * atr, nearest_support * 0.99), 2)
        target_price = round(min(price + 3 * atr, nearest_resistance), 2)
        confidence = round(buy_prob * 100, 1)
        reason = f"AI模型预测买入概率 {buy_prob*100:.1f}%"
        factors = [f"买入概率: {buy_prob*100:.1f}%", f"卖出概率: {sell_prob*100:.1f}%"]
        if trend == "up":
            factors.append("趋势向上")
            confidence = min(confidence + 10, 100)
    elif sell_prob >= sell_threshold and sell_prob > buy_prob:
        action = "sell"
        entry_price = round(price, 2)
        stop_loss = round(price + 2 * atr, 2)
        target_price = round(max(price - 3 * atr, nearest_support), 2)
        confidence = round(sell_prob * 100, 1)
        reason = f"AI模型预测卖出概率 {sell_prob*100:.1f}%"
        factors = [f"卖出概率: {sell_prob*100:.1f}%", f"买入概率: {buy_prob*100:.1f}%"]
        if trend == "down":
            factors.append("趋势向下")
            confidence = min(confidence + 10, 100)
    else:
        action = "hold"
        entry_price = round(price, 2)
        stop_loss = round(max(price - 2 * atr, nearest_support * 0.99), 2)
        target_price = round(min(price + 3 * atr, nearest_resistance), 2)
        confidence = 0
        reason = f"AI模型未给出明确信号 (买:{buy_prob*100:.1f}% 卖:{sell_prob*100:.1f}%)"
        factors = ["信号不明确，建议观望"]

    risk_pct = round(abs(entry_price - stop_loss) / entry_price * 100, 2) if entry_price > 0 else 0
    reward_pct = round(abs(target_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0
    risk_reward = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0

    # Position sizing (2% account risk)
    position_pct = round(min(2.0 / risk_pct * 100, 100), 1) if risk_pct > 0 else 0
    position_pct = round(position_pct * min(confidence, 80) / 80, 1)

    result = {
        "action": action,
        "model_type": model_type,
        "reason": reason,
        "confidence": confidence,
        "current_price": round(price, 2),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "risk_reward": risk_reward,
        "nearest_support": round(nearest_support, 2),
        "nearest_resistance": round(nearest_resistance, 2),
        "atr": round(atr, 2),
        "trend": trend,
        "suggested_position_pct": position_pct,
        "factors": factors,
        "probabilities": {
            "hold": pred["hold_prob"],
            "buy": pred["buy_prob"],
            "sell": pred["sell_prob"],
        },
    }
    # Include ensemble voting details if applicable
    if model_type == "ensemble" and "model_details" in pred:
        result["ensemble"] = {
            "models_used": pred["models_used"],
            "buy_votes": pred["buy_votes"],
            "sell_votes": pred["sell_votes"],
            "model_details": pred["model_details"],
        }
    return result


# ─────────────────────────── AI Backtest ───────────────────────────

def ai_backtest(
    candles: list[Candle],
    model_type: ModelType = "lightgbm",
    model_key: str | None = None,
    buy_threshold: float = 0.4,
    sell_threshold: float = 0.4,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 3.0,
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    max_hold_bars: int = 20,
) -> dict:
    """Run backtest using AI model predictions as entry/exit signals."""
    _default_keys = {"lightgbm": "ai_lgb", "transformer": "ai_trf", "lstm": "ai_lstm", "cnn_lstm": "ai_cnn_lstm", "rl_ppo": "ai_rl"}
    key = model_key or _default_keys.get(model_type, f"ai_{model_type}")
    start_idx = max(60, SEQ_LEN + 60) if model_type in ("transformer", "lstm", "cnn_lstm", "ensemble", "rl_ppo") else 60

    # Pre-compute all predictions
    predictions: dict[int, dict] = {}
    for i in range(start_idx, len(candles)):
        sub = candles[:i + 1]
        if model_type == "ensemble":
            pred = predict_ensemble(sub)
        else:
            pred = predict(sub, model_type, key)
        if pred:
            predictions[i] = pred

    if not predictions:
        return {"error": "model not trained or insufficient data"}

    trades: list[dict] = []
    equity = [1.0]
    benchmark = [1.0]
    in_trade = False
    entry_idx = 0
    entry_price = 0.0
    cost_pct = (commission_pct + slippage_pct * 2) / 100

    for i in range(start_idx, len(candles)):
        # Benchmark (buy & hold from start)
        if i > start_idx:
            bm_ret = candles[i].close / candles[i - 1].close
            benchmark.append(benchmark[-1] * bm_ret)
        else:
            benchmark.append(1.0)

        pred = predictions.get(i)
        if pred is None:
            equity.append(equity[-1])
            continue

        if not in_trade:
            if pred["buy_prob"] >= buy_threshold and pred["buy_prob"] > pred["sell_prob"]:
                in_trade = True
                entry_idx = i
                entry_price = candles[i].close
                equity.append(equity[-1])
            else:
                equity.append(equity[-1])
        else:
            bar = candles[i]
            pnl_pct = (bar.close / entry_price - 1) * 100

            # Exit conditions
            atr = 0.0
            if i >= 15:
                trs = []
                for j in range(i - 14, i):
                    h, lo, pc = candles[j].high, candles[j].low, candles[j - 1].close
                    trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
                atr = float(np.mean(trs))

            stop_pct = -(stop_atr_mult * atr / entry_price * 100) if atr > 0 else -5
            target_pct = (target_atr_mult * atr / entry_price * 100) if atr > 0 else 10

            reason = ""
            should_exit = False

            if pnl_pct <= stop_pct:
                should_exit = True
                reason = f"止损 ({pnl_pct:.1f}%)"
            elif pnl_pct >= target_pct:
                should_exit = True
                reason = f"止盈 ({pnl_pct:.1f}%)"
            elif pred["sell_prob"] >= sell_threshold:
                should_exit = True
                reason = f"AI卖出信号 (卖出概率{pred['sell_prob']*100:.0f}%)"
            elif i - entry_idx >= max_hold_bars:
                should_exit = True
                reason = f"持有超限 ({max_hold_bars}天)"

            if should_exit:
                net_pnl = pnl_pct - cost_pct * 100
                trades.append({
                    "entry_date": candles[entry_idx].date,
                    "entry_price": round(entry_price, 2),
                    "exit_date": bar.date,
                    "exit_price": round(bar.close, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_net": round(net_pnl, 2),
                    "holding_bars": i - entry_idx,
                    "reason_exit": reason,
                })
                equity.append(equity[-1] * (1 + net_pnl / 100))
                in_trade = False
            else:
                equity.append(equity[-1] * (1 + (bar.close / candles[i - 1].close - 1)))

    # Stats
    wins = [t for t in trades if t["pnl_net"] > 0]
    losses = [t for t in trades if t["pnl_net"] <= 0]

    equity_arr = np.array(equity)
    peak = np.maximum.accumulate(equity_arr)
    drawdowns = (equity_arr - peak) / peak
    max_dd = float(np.min(drawdowns)) * 100

    total_return = (equity[-1] / equity[0] - 1) * 100 if equity[0] > 0 else 0
    bm_return = (benchmark[-1] / benchmark[0] - 1) * 100 if benchmark[0] > 0 else 0

    # Sharpe
    if len(equity) > 2:
        daily_rets = np.diff(equity_arr) / equity_arr[:-1]
        sharpe = float(np.mean(daily_rets) / max(np.std(daily_rets), 1e-9) * np.sqrt(250))
    else:
        sharpe = 0.0

    # Equity curve (sampled)
    eq_dates = [candles[i].date for i in range(start_idx, len(candles))]
    eq_curve = []
    step = max(1, len(eq_dates) // 200)
    for j in range(0, len(eq_dates), step):
        eq_curve.append({
            "date": eq_dates[j],
            "equity": round(equity[j + 1] if j + 1 < len(equity) else equity[-1], 4),
            "benchmark": round(benchmark[j + 1] if j + 1 < len(benchmark) else benchmark[-1], 4),
        })

    return {
        "model_type": model_type,
        "trades": trades,
        "equity_curve": eq_curve,
        "stats": {
            "total_trades": len(trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / max(len(trades), 1), 4),
            "avg_win": round(float(np.mean([t["pnl_net"] for t in wins])), 2) if wins else 0,
            "avg_loss": round(float(np.mean([t["pnl_net"] for t in losses])), 2) if losses else 0,
            "profit_factor": round(
                abs(sum(t["pnl_net"] for t in wins)) / max(abs(sum(t["pnl_net"] for t in losses)), 0.01), 2
            ) if trades else 0,
            "max_drawdown": round(max_dd, 2),
            "total_return": round(total_return, 2),
            "benchmark_return": round(bm_return, 2),
            "sharpe": round(sharpe, 2),
        },
    }


def model_status() -> dict:
    """Check which AI strategy models are trained."""
    lgb_path = _MODEL_DIR / "ai_lgb.pkl"
    trf_path = _MODEL_DIR / "ai_trf.pt"
    lstm_path = _MODEL_DIR / "ai_lstm.pt"
    cnn_lstm_path = _MODEL_DIR / "ai_cnn_lstm.pt"
    rl_path = _MODEL_DIR / "ai_rl.zip"
    return {
        "lightgbm": {
            "trained": lgb_path.exists(),
            "path": str(lgb_path) if lgb_path.exists() else None,
        },
        "transformer": {
            "trained": trf_path.exists(),
            "path": str(trf_path) if trf_path.exists() else None,
        },
        "lstm": {
            "trained": lstm_path.exists(),
            "path": str(lstm_path) if lstm_path.exists() else None,
        },
        "cnn_lstm": {
            "trained": cnn_lstm_path.exists(),
            "path": str(cnn_lstm_path) if cnn_lstm_path.exists() else None,
        },
        "rl_ppo": {
            "trained": rl_path.exists(),
            "path": str(rl_path) if rl_path.exists() else None,
        },
    }
