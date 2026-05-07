"""P4 — CNN-based pattern recognition on OHLCV sequences.

Converts K-line sequences to fixed-size windows and classifies them
into pattern types using a 1D-CNN (PyTorch).

Also includes a DTW (Dynamic Time Warping) baseline for template matching
when no trained model is available.

Patterns detected:
  0 – 无形态 (no pattern)
  1 – 双底 (double bottom)
  2 – 头肩底 (head and shoulders bottom)
  3 – 杯柄 (cup and handle)
  4 – 突破回踩 (breakout pullback)
  5 – 下跌企稳 (bottom stabilize)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ..schemas import Candle

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("/tmp/pivotlab_models")
_MODEL_DIR.mkdir(exist_ok=True)

PATTERN_NAMES = {
    0: "无形态",
    1: "双底",
    2: "头肩底",
    3: "杯柄",
    4: "突破回踩",
    5: "下跌企稳",
}

WINDOW_SIZE = 30  # bars per sample


# ── Feature preparation ──

def _candles_to_array(candles: list[Candle], window: int = WINDOW_SIZE) -> np.ndarray | None:
    """Convert last *window* candles to normalised (window, 5) array [O,H,L,C,V]."""
    if len(candles) < window:
        return None
    subset = candles[-window:]
    arr = np.array([[c.open, c.high, c.low, c.close, c.volume] for c in subset], dtype=np.float64)
    # Normalise price cols by first close
    base_price = arr[0, 3]
    if base_price <= 0:
        return None
    arr[:, :4] /= base_price
    # Normalise volume by mean
    mean_vol = np.mean(arr[:, 4])
    if mean_vol > 0:
        arr[:, 4] /= mean_vol
    return arr.astype(np.float32)


# ── DTW template matching (no training needed) ──

def _dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Simple DTW distance on 1D sequences."""
    n, m = len(a), len(b)
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(a[i - 1] - b[j - 1])
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])
    return float(dtw[n, m])


def _generate_template(pattern_id: int, length: int = 30) -> np.ndarray:
    """Generate synthetic price template for a pattern."""
    t = np.linspace(0, 1, length)
    if pattern_id == 1:  # double bottom: W shape
        return 1.0 - 0.15 * (np.sin(2 * np.pi * t) ** 2) + 0.02 * t
    elif pattern_id == 2:  # head & shoulders bottom: deeper middle dip
        left = np.where(t < 0.33, 1.0 - 0.1 * np.sin(np.pi * t / 0.33), 0)
        mid = np.where((t >= 0.33) & (t < 0.67), 1.0 - 0.18 * np.sin(np.pi * (t - 0.33) / 0.34), 0)
        right = np.where(t >= 0.67, 1.0 - 0.1 * np.sin(np.pi * (t - 0.67) / 0.33), 0)
        return left + mid + right
    elif pattern_id == 3:  # cup & handle: U shape + small dip
        cup = 1.0 - 0.15 * (1 - (2 * np.minimum(t, 0.75) / 0.75 - 1) ** 2)
        handle = np.where(t > 0.8, -0.03 * np.sin(np.pi * (t - 0.8) / 0.2), 0)
        return cup + handle
    elif pattern_id == 4:  # breakout pullback: rise then shallow dip
        return np.where(t < 0.6, 1.0 + 0.15 * t / 0.6,
                        1.15 - 0.05 * np.sin(np.pi * (t - 0.6) / 0.4))
    elif pattern_id == 5:  # bottom stabilize: decline then flat
        return np.where(t < 0.5, 1.0 - 0.2 * t / 0.5, 0.8 + 0.02 * np.random.randn())
    return np.ones(length)  # no pattern


_TEMPLATES = {pid: _generate_template(pid) for pid in range(1, 6)}


def dtw_classify(candles: list[Candle], top_k: int = 3) -> list[dict]:
    """Classify pattern using DTW template matching.
    Returns top-k matches sorted by similarity (lower distance = better).
    """
    arr = _candles_to_array(candles)
    if arr is None:
        return []
    close_seq = arr[:, 3]  # normalised closes

    results = []
    for pid, template in _TEMPLATES.items():
        dist = _dtw_distance(close_seq, template)
        # Convert distance to similarity score (0-100)
        # Use exponential decay: score = 100 * exp(-dist)
        score = max(0, 100 * np.exp(-dist))
        results.append({
            "pattern_id": pid,
            "pattern_name": PATTERN_NAMES[pid],
            "similarity": round(score, 1),
            "dtw_distance": round(dist, 4),
        })
    results.sort(key=lambda x: -x["similarity"])
    return results[:top_k]


# ── CNN model (PyTorch) ──

def _build_cnn():
    """Build a 1D-CNN for pattern classification."""
    import torch
    import torch.nn as nn

    class PatternCNN(nn.Module):
        def __init__(self, in_channels=5, n_classes=6, seq_len=WINDOW_SIZE):
            super().__init__()
            self.conv = nn.Sequential(
                nn.Conv1d(in_channels, 32, kernel_size=3, padding=1),
                nn.BatchNorm1d(32),
                nn.ReLU(),
                nn.MaxPool1d(2),
                nn.Conv1d(32, 64, kernel_size=3, padding=1),
                nn.BatchNorm1d(64),
                nn.ReLU(),
                nn.MaxPool1d(2),
                nn.Conv1d(64, 128, kernel_size=3, padding=1),
                nn.BatchNorm1d(128),
                nn.ReLU(),
                nn.AdaptiveAvgPool1d(1),
            )
            self.fc = nn.Sequential(
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(64, n_classes),
            )

        def forward(self, x):
            # x: (batch, channels, seq_len)
            x = self.conv(x)
            x = x.squeeze(-1)
            return self.fc(x)

    return PatternCNN()


def _generate_synthetic_data(n_per_class: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Generate synthetic training data by creating noisy variants of templates."""
    X_list, y_list = [], []
    for pid in range(6):
        for _ in range(n_per_class):
            if pid == 0:
                # Random walk (no pattern)
                base = np.cumsum(np.random.randn(WINDOW_SIZE) * 0.01) + 1.0
            else:
                template = _generate_template(pid, WINDOW_SIZE)
                noise = np.random.randn(WINDOW_SIZE) * 0.015
                stretch = 1.0 + np.random.uniform(-0.05, 0.05)
                base = template * stretch + noise

            # Build OHLCV from close
            closes = base
            noise_hl = np.abs(np.random.randn(WINDOW_SIZE) * 0.005)
            highs = closes + noise_hl
            lows = closes - noise_hl
            opens = np.roll(closes, 1)
            opens[0] = closes[0]
            volumes = np.abs(1.0 + np.random.randn(WINDOW_SIZE) * 0.3)
            sample = np.stack([opens, highs, lows, closes, volumes], axis=0)  # (5, 30)
            X_list.append(sample)
            y_list.append(pid)
    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int64)


def train_cnn(
    n_per_class: int = 300,
    epochs: int = 30,
    model_key: str = "pattern_cnn",
) -> dict:
    """Train CNN on synthetic pattern data. Returns training stats."""
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    X, y = _generate_synthetic_data(n_per_class)
    # Shuffle
    perm = np.random.permutation(len(X))
    X, y = X[perm], y[perm]

    split = int(len(X) * 0.8)
    X_train, X_test = torch.tensor(X[:split]), torch.tensor(X[split:])
    y_train, y_test = torch.tensor(y[:split]), torch.tensor(y[split:])

    train_ds = TensorDataset(X_train, y_train)
    train_dl = DataLoader(train_ds, batch_size=32, shuffle=True)

    model = _build_cnn()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    for epoch in range(epochs):
        model.train()
        for xb, yb in train_dl:
            pred = model(xb)
            loss = criterion(pred, yb)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

    # Evaluate
    model.eval()
    with torch.no_grad():
        pred = model(X_test).argmax(dim=1)
        acc = float((pred == y_test).float().mean())

    # Per-class accuracy
    class_acc = {}
    for pid in range(6):
        mask = y_test == pid
        if mask.sum() > 0:
            class_acc[PATTERN_NAMES[pid]] = round(float((pred[mask] == pid).float().mean()), 4)

    # Save
    torch.save(model.state_dict(), _MODEL_DIR / f"{model_key}.pt")

    return {
        "samples": len(X),
        "epochs": epochs,
        "accuracy": round(acc, 4),
        "class_accuracy": class_acc,
    }


def cnn_classify(candles: list[Candle], model_key: str = "pattern_cnn") -> list[dict] | None:
    """Classify pattern using trained CNN.  Returns all class probabilities."""
    import torch

    arr = _candles_to_array(candles)
    if arr is None:
        return None

    model = _build_cnn()
    model_path = _MODEL_DIR / f"{model_key}.pt"
    if not model_path.exists():
        return None
    model.load_state_dict(torch.load(model_path, weights_only=True))
    model.eval()

    # arr is (30, 5), need (1, 5, 30)
    x = torch.tensor(arr.T).unsqueeze(0)
    with torch.no_grad():
        logits = model(x)
        probs = torch.softmax(logits, dim=1).squeeze().numpy()

    results = []
    for pid in range(len(probs)):
        results.append({
            "pattern_id": pid,
            "pattern_name": PATTERN_NAMES.get(pid, f"P{pid}"),
            "probability": round(float(probs[pid]) * 100, 1),
        })
    results.sort(key=lambda x: -x["probability"])
    return results
