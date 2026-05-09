"""Tiny Temporal Convolutional Network on 60-bar OHLCV windows.

Why TCN over Transformer? On daily-bar A-share data with ~30k training
samples, a small TCN converges fast, doesn't overfit, and avoids needing a
GPU for inference.  We do offer GPU training if torch detects CUDA.

Public API
----------
train(...) -> dict
predict_score_0_100(window: np.ndarray) -> float | None     # window: (60,5)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from . import dataset as ds
from . import registry

logger = logging.getLogger(__name__)

NAME = "seq_tcn"
SEQ_LEN = 60
N_CHANNELS = 5  # OHLCV


def _model_path() -> Path:
    return registry.model_dir(NAME) / "model.pt"


# ──────────────────────────────────────────────────────────────
#  Model definition
# ──────────────────────────────────────────────────────────────
def _build_model():
    import torch
    import torch.nn as nn

    class TCNBlock(nn.Module):
        def __init__(self, c_in, c_out, dilation, k=3):
            super().__init__()
            pad = (k - 1) * dilation
            self.conv1 = nn.Conv1d(c_in, c_out, k, padding=pad, dilation=dilation)
            self.conv2 = nn.Conv1d(c_out, c_out, k, padding=pad, dilation=dilation)
            self.act = nn.GELU()
            self.drop = nn.Dropout(0.1)
            self.skip = nn.Conv1d(c_in, c_out, 1) if c_in != c_out else nn.Identity()

        def forward(self, x):
            seq = x.shape[-1]
            h = self.conv1(x)[..., :seq]
            h = self.act(h)
            h = self.drop(h)
            h = self.conv2(h)[..., :seq]
            return self.act(h + self.skip(x))

    class TCN(nn.Module):
        def __init__(self):
            super().__init__()
            self.b1 = TCNBlock(N_CHANNELS, 32, 1)
            self.b2 = TCNBlock(32, 64, 2)
            self.b3 = TCNBlock(64, 64, 4)
            self.b4 = TCNBlock(64, 64, 8)
            self.head = nn.Sequential(
                nn.AdaptiveAvgPool1d(1), nn.Flatten(),
                nn.Linear(64, 32), nn.GELU(), nn.Dropout(0.1),
                nn.Linear(32, 1),
            )

        def forward(self, x):                # x: (B, 5, 60)
            h = self.b1(x); h = self.b2(h)
            h = self.b3(h); h = self.b4(h)
            return self.head(h).squeeze(-1)  # (B,)

    return TCN()


# ──────────────────────────────────────────────────────────────
#  Training
# ──────────────────────────────────────────────────────────────
def train(
    *,
    horizon_days: int = 10,
    epochs: int = 12,
    batch_size: int = 256,
    lr: float = 1e-3,
    universe_limit: int | None = 600,
    history_years: float = 2.0,
    progress_cb=None,
) -> dict:
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    if progress_cb:
        progress_cb({"phase": "build_dataset", "pct": 5})

    data = ds.build_dataset(
        horizon_days=horizon_days,
        snapshot_step=5,
        history_years=history_years,
        universe_limit=universe_limit,
        progress_cb=progress_cb,
    )
    train_set, val_set = data.split_time(val_frac=0.2)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("seq.train: device=%s", device)

    Xtr = torch.from_numpy(train_set.seq).float().permute(0, 2, 1)  # (N,5,60)
    ytr = torch.from_numpy(train_set.y_ret).float()
    Xva = torch.from_numpy(val_set.seq).float().permute(0, 2, 1)
    yva = torch.from_numpy(val_set.y_ret).float()

    train_loader = DataLoader(TensorDataset(Xtr, ytr), batch_size=batch_size,
                              shuffle=True, num_workers=0)
    val_loader = DataLoader(TensorDataset(Xva, yva), batch_size=batch_size,
                            shuffle=False, num_workers=0)

    model = _build_model().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    crit = torch.nn.SmoothL1Loss()

    best_val = float("inf")
    best_state = None
    history = []

    for epoch in range(epochs):
        model.train()
        tr_loss = 0.0
        n = 0
        for xb, yb in train_loader:
            xb = xb.to(device); yb = yb.to(device)
            opt.zero_grad()
            pred = model(xb)
            loss = crit(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tr_loss += float(loss.detach().cpu()) * xb.size(0)
            n += xb.size(0)
        sched.step()
        tr_loss /= max(n, 1)

        model.eval()
        va_loss = 0.0; m = 0
        preds_all = []; targets_all = []
        with torch.no_grad():
            for xb, yb in val_loader:
                xb = xb.to(device); yb = yb.to(device)
                p = model(xb)
                va_loss += float(crit(p, yb).cpu()) * xb.size(0)
                m += xb.size(0)
                preds_all.append(p.cpu().numpy())
                targets_all.append(yb.cpu().numpy())
        va_loss /= max(m, 1)
        ic = _spearman(np.concatenate(preds_all), np.concatenate(targets_all))
        history.append({"epoch": epoch + 1, "train_loss": round(tr_loss, 3),
                        "val_loss": round(va_loss, 3), "val_ic": round(ic, 4)})
        logger.info("seq epoch %d/%d  train=%.3f val=%.3f ic=%.4f",
                    epoch + 1, epochs, tr_loss, va_loss, ic)
        if progress_cb:
            progress_cb({
                "phase": "fit_seq", "pct": 30 + int(60 * (epoch + 1) / epochs),
                "epoch": epoch + 1, "epochs": epochs,
                "train_loss": round(tr_loss, 3), "val_loss": round(va_loss, 3),
                "val_ic": round(ic, 4),
            })

        if va_loss < best_val:
            best_val = va_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    # Compute final val pred std for inference scaling
    model.eval()
    with torch.no_grad():
        preds = []
        for xb, _ in val_loader:
            preds.append(model(xb.to(device)).cpu().numpy())
        preds_np = np.concatenate(preds)

    torch.save({
        "state_dict": model.state_dict(),
        "scaler": {
            "pred_mean": float(preds_np.mean()),
            "pred_std": float(preds_np.std() + 1e-6),
        },
    }, str(_model_path()))

    final_ic = history[-1]["val_ic"] if history else 0.0
    meta = {
        "model": NAME,
        "epochs_run": epochs,
        "device": device,
        "samples_train": int(Xtr.shape[0]),
        "samples_val": int(Xva.shape[0]),
        "best_val_loss": round(float(best_val), 3),
        "final_val_ic": round(float(final_ic), 4),
        "history": history,
    }
    registry.write_meta(NAME, meta)
    logger.info("seq trained: %s", {k: v for k, v in meta.items() if k != "history"})

    if progress_cb:
        progress_cb({"phase": "done", "pct": 100, **{k: v for k, v in meta.items() if k != "history"}})

    global _model, _scaler
    _model = None
    _scaler = None
    return meta


# ──────────────────────────────────────────────────────────────
#  Inference
# ──────────────────────────────────────────────────────────────
_model = None
_scaler = None
_torch_device = None


def is_trained() -> bool:
    return _model_path().exists()


def _load() -> bool:
    global _model, _scaler, _torch_device
    if _model is not None:
        return True
    p = _model_path()
    if not p.exists():
        return False
    import torch
    _torch_device = "cuda" if torch.cuda.is_available() else "cpu"
    payload = torch.load(str(p), map_location=_torch_device, weights_only=False)
    m = _build_model().to(_torch_device)
    m.load_state_dict(payload["state_dict"])
    m.eval()
    _model = m
    _scaler = payload.get("scaler", {"pred_mean": 0.0, "pred_std": 1.0})
    return True


def predict_score_0_100(window: np.ndarray) -> float | None:
    """window: (60,5) raw OHLCV. Returns 0-100 calibrated score."""
    if not _load():
        return None
    if window.shape != (SEQ_LEN, N_CHANNELS):
        return None
    import torch
    norm = ds.normalize_window(window)
    x = torch.from_numpy(norm).float().permute(1, 0).unsqueeze(0).to(_torch_device)  # (1,5,60)
    with torch.no_grad():
        raw = float(_model(x).cpu().numpy()[0])
    z = (raw - _scaler["pred_mean"]) / max(_scaler["pred_std"], 1e-6)
    s = 1.0 / (1.0 + math.exp(-z))
    return float(np.clip(s * 100.0, 0.0, 100.0))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return 0.0
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])
