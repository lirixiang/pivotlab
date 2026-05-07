"""API routes for algorithmic strategy modules (P0–P4)."""
import asyncio
import logging

from fastapi import APIRouter

from ..services.data_provider import get_candles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/algo", tags=["algo"])


# ── P0: Optuna parameter optimisation ──

@router.post("/optimize")
async def optimize(body: dict):
    """Optimise backtest parameters for a stock."""
    code = body.get("code", "000001")
    strategy = body.get("strategy", "breakout_pullback")
    period = body.get("period", "3m")
    n_trials = min(int(body.get("n_trials", 60)), 200)
    target = body.get("target", "sharpe")

    from ..services.optimizer import optimise_params

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, optimise_params, candles, strategy, period, n_trials, target,
    )
    result["code"] = code
    result["strategy"] = strategy
    return result


# ── P1: LightGBM ML scoring ──

@router.post("/ml/train")
async def ml_train(body: dict):
    """Train LightGBM scoring model on given stock codes."""
    codes = body.get("codes", [])
    forward_days = int(body.get("forward_days", 5))
    profit_threshold = float(body.get("profit_threshold", 3.0))

    if not codes:
        return {"error": "provide a list of stock codes"}

    from ..services.ml_scorer import train_model

    candle_lists = []
    for code in codes[:50]:  # cap at 50 stocks
        c = get_candles(code, period="daily", days=500)
        if c:
            candle_lists.append(c)

    if not candle_lists:
        return {"error": "no candle data for given codes"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, train_model, candle_lists, forward_days, profit_threshold, "default",
    )
    return result


@router.get("/ml/score/{code}")
async def ml_score(code: str):
    """Get ML-based signal score for latest bar."""
    from ..services.ml_scorer import predict_score

    candles = get_candles(code, period="daily", days=120)
    if not candles:
        return {"error": "no candle data"}

    score = predict_score(candles)
    if score is None:
        return {"error": "model not trained yet — call POST /api/algo/ml/train first"}
    return {"code": code, "ml_score": score}


# ── P2: RL dynamic position sizing ──

@router.post("/rl/train")
async def rl_train(body: dict):
    """Train RL agent for position sizing."""
    codes = body.get("codes", [])
    total_timesteps = min(int(body.get("total_timesteps", 50000)), 200000)

    if not codes:
        return {"error": "provide a list of stock codes"}

    from ..services.rl_agent import train_rl_agent

    candle_lists = []
    for code in codes[:20]:
        c = get_candles(code, period="daily", days=500)
        if c:
            candle_lists.append(c)

    if not candle_lists:
        return {"error": "no candle data"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, train_rl_agent, candle_lists, total_timesteps, "rl_ppo",
    )
    return result


@router.get("/rl/position/{code}")
async def rl_position(code: str):
    """Get RL-suggested position size for latest bar."""
    from ..services.rl_agent import rl_position_size

    candles = get_candles(code, period="daily", days=120)
    if not candles:
        return {"error": "no candle data"}

    result = rl_position_size(candles)
    if result is None:
        return {"error": "RL model not trained yet — call POST /api/algo/rl/train first"}
    result["code"] = code
    return result


# ── P3: HMM market regime ──

@router.post("/regime/fit")
async def regime_fit(body: dict):
    """Fit HMM regime model on index or stock data."""
    code = body.get("code", "000001")  # default: 上证指数
    n_regimes = min(int(body.get("n_regimes", 3)), 5)

    from ..services.regime_hmm import fit_hmm

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, fit_hmm, candles, n_regimes, "hmm_regime")
    result["code"] = code
    return result


@router.get("/regime/{code}")
async def regime_predict(code: str):
    """Predict current regime for a stock/index."""
    from ..services.regime_hmm import predict_regime

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    result = predict_regime(candles)
    if result is None:
        return {"error": "HMM model not fitted yet — call POST /api/algo/regime/fit first"}
    result["code"] = code
    return result


# ── P4: CNN/DTW pattern recognition ──

@router.get("/pattern/dtw/{code}")
async def pattern_dtw(code: str, top_k: int = 3):
    """Classify pattern using DTW template matching (no training needed)."""
    from ..services.pattern_cnn import dtw_classify

    candles = get_candles(code, period="daily", days=60)
    if not candles:
        return {"error": "no candle data"}

    results = dtw_classify(candles, top_k=top_k)
    return {"code": code, "method": "dtw", "patterns": results}


@router.post("/pattern/cnn/train")
async def pattern_cnn_train(body: dict):
    """Train CNN pattern classifier on synthetic data."""
    n_per_class = min(int(body.get("n_per_class", 300)), 1000)
    epochs = min(int(body.get("epochs", 30)), 100)

    from ..services.pattern_cnn import train_cnn

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, train_cnn, n_per_class, epochs, "pattern_cnn")
    return result


@router.get("/pattern/cnn/{code}")
async def pattern_cnn_predict(code: str):
    """Classify pattern using trained CNN."""
    from ..services.pattern_cnn import cnn_classify

    candles = get_candles(code, period="daily", days=60)
    if not candles:
        return {"error": "no candle data"}

    results = cnn_classify(candles)
    if results is None:
        return {"error": "CNN model not trained yet — call POST /api/algo/pattern/cnn/train first"}
    return {"code": code, "method": "cnn", "patterns": results}


# ── Summary: available algo modules ──

@router.get("/status")
async def algo_status():
    """Check which algo modules are available / trained."""
    from pathlib import Path
    model_dir = Path("/tmp/pivotlab_models")
    return {
        "modules": [
            {
                "id": "P0", "name": "参数自优化 (Optuna)",
                "status": "ready", "needs_training": False,
                "description": "使用贝叶斯优化搜索最优回测参数",
            },
            {
                "id": "P1", "name": "ML 信号打分 (LightGBM)",
                "status": "trained" if (model_dir / "default.pkl").exists() else "untrained",
                "needs_training": True,
                "description": "用历史回报自动标注，训练梯度提升模型替代规则打分",
            },
            {
                "id": "P2", "name": "RL 动态仓位 (PPO)",
                "status": "trained" if (model_dir / "rl_ppo.zip").exists() else "untrained",
                "needs_training": True,
                "description": "强化学习代理，动态调整仓位比例",
            },
            {
                "id": "P3", "name": "市场状态 (HMM)",
                "status": "fitted" if (model_dir / "hmm_regime.pkl").exists() else "unfitted",
                "needs_training": True,
                "description": "隐马尔可夫模型，识别趋势/震荡/危机市场状态",
            },
            {
                "id": "P4", "name": "形态识别 (CNN+DTW)",
                "status": "trained" if (model_dir / "pattern_cnn.pt").exists() else "dtw_only",
                "needs_training": True,
                "description": "DTW模板匹配(即用) + CNN深度学习(需训练)",
            },
        ],
    }
