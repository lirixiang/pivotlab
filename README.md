---
title: PivotLab
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# PivotLab 智线

> **自动支撑压力位 · 形态筛选 · AI 策略训练 · 多 GPU DDP** —— 面向 A 股技术分析散户的开源辅助决策终端。

## ✨ 核心能力

- **自动画线**：基于 `scipy` 局部极值 + 价位聚类 + 均线动态，自动识别 R1/R2/R3 / S1/S2/S3 多档支撑压力位，附强度星级（1-5）和触及次数。
- **形态筛选**：内置「**突破回踩**」「**下跌企稳**」「**箱体支撑**」「**临近中强支撑**」四大量化形态扫描器（对齐 stock-sr-platform），每只股票计算 0-100 综合评分。
- **AI 策略训练**：5 种模型（Transformer / LSTM / CNN-LSTM / LightGBM / RL-PPO），支持单 GPU 和多 GPU DDP 分布式训练，Ray 统一调度。
- **专业终端 UI**：深色三栏布局（自选 + 主图 + 信号面板），金色压力位 / 天青蓝支撑位双色体系，对标 Bloomberg / TradingView 视觉规范。
- **开源数据**：使用 `akshare` 公开行情；网络不可用时自动回退到确定性 mock 数据，保证开发与演示永远可跑。

## 🏗️ 架构

```
pivotlab/
├── backend/                    FastAPI + Ray + PyTorch + scipy + akshare
│   ├── app/
│   │   ├── routers/            market / stocks / screener / watchlist / strategy
│   │   ├── services/
│   │   │   ├── data_provider.py    akshare 适配 + mock 回退 + DB 缓存
│   │   │   ├── levels.py           支撑压力位识别算法
│   │   │   ├── screener.py         形态扫描器
│   │   │   ├── ai_strategy.py      5 种 AI 模型定义 & 训练逻辑
│   │   │   ├── ray_trainer.py      Ray 分布式训练调度 (单 GPU / DDP)
│   │   │   └── rl_strategy.py      RL-PPO 强化学习策略
│   │   └── main.py                 Ray 初始化 (6 GPU)
│   ├── train_cli.py                独立 CLI 训练脚本 (支持 DDP)
│   └── models/                     训练产物 (.pt / .pkl)
├── frontend/                   Vite + React 18 + TS + Tailwind 3
│   └── src/
│       ├── components/         TopBar / Watchlist / Chart / Signal / Screener
│       ├── pages/StrategyPage  AI 策略训练界面 (GPU 选择 / 进度)
│       ├── services/api.ts
│       └── App.tsx
├── prototype/                  静态 HTML 原型（设计基线）
├── Dockerfile                  单镜像：前端构建 + nginx + uvicorn
├── docker-compose.yml          app + PostgreSQL，支持 NVIDIA GPU
└── Makefile                    常用命令
```

## 🖥️ 硬件要求

| 组件 | 最低要求 | 推荐配置 |
|---|---|---|
| GPU | 1× NVIDIA GPU (≥8GB) | 多卡 (如 6× RTX 3080 Ti) |
| 显存 | 8 GB | 11 GB × N |
| 内存 | 16 GB | 64 GB+ |
| Docker | 20.10+ (nvidia-container-toolkit) | 27.x + nvidia runtime |
| CUDA | 12.0+ | 12.8+ |

## 🚀 快速开始

### Docker 部署（推荐）

```bash
cd pivotlab

# 启动 (自动构建镜像，挂载 GPU)
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止
docker-compose down
```

启动后：
- `http://localhost:9173` — 前端页面（nginx 反代 `/api`）
- `http://localhost:18080/api/health` — 直连 FastAPI 后端

### 本地开发

```bash
# 1. 后端
cd backend
pip install -r requirements.txt
python run.py            # http://localhost:8000

# 2. 前端
cd frontend
npm install
npm run dev              # http://localhost:5173
```

## 🤖 AI 策略训练

### 支持的模型

| 模型 | 类型 | 设备 | 说明 |
|---|---|---|---|
| `transformer` | 时序分类 | GPU | 多头自注意力，捕捉长周期特征 |
| `lstm` | 时序分类 | GPU | 双层 LSTM + 全连接 |
| `cnn_lstm` | 时序分类 | GPU | 1D 卷积提取局部特征 + LSTM 时序建模 |
| `lightgbm` | 梯度提升树 | CPU | 高效基线模型，特征工程驱动 |
| `rl_ppo` | 强化学习 | GPU | PPO 策略优化，直接学习交易动作 |

### Web 界面训练

通过前端 **策略页面** 提交训练任务：
- 选择模型类型、股票数量、训练轮数
- 选择 GPU 数量（1 = 单卡，2-6 = DDP 多卡并行）
- 实时查看训练进度、loss、accuracy

### API 训练

```bash
# 单 GPU 训练
curl -X POST http://localhost:18080/api/strategy/train_market \
  -H "Content-Type: application/json" \
  -d '{"model_type":"transformer","max_stocks":200,"epochs":50,"num_gpus":1}'

# 多 GPU DDP 训练 (3 卡)
curl -X POST http://localhost:18080/api/strategy/train_market \
  -H "Content-Type: application/json" \
  -d '{"model_type":"transformer","max_stocks":200,"epochs":50,"num_gpus":3}'

# 查看进度
curl http://localhost:18080/api/strategy/train_progress

# 全部 5 个模型并行训练
curl -X POST http://localhost:18080/api/strategy/train_market \
  -H "Content-Type: application/json" \
  -d '{"model_type":"all","max_stocks":200,"epochs":50,"num_gpus":1}'
```

### CLI 独立训练

在 trainer 容器内执行（有 GPU 支持）：

```bash
export DOCKER_HOST=unix:///var/run/docker.sock

# ── LightGBM (CPU, 快) ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model lightgbm --stocks 1000 --epochs 100 --gpu -1

# ── Transformer (多卡 DDP) ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model transformer --stocks 500 --epochs 50 --num-gpus 4

# ── LSTM (单卡) ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model lstm --stocks 500 --epochs 50 --gpu 0

# ── CNN-LSTM (单卡) ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model cnn_lstm --stocks 500 --epochs 50 --gpu 1

# ── RL-PPO (CPU, 控制规模避免 OOM) ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model rl_ppo --stocks 30 --epochs 50 --gpu -1

# ── 全部模型 (PyTorch 走 DDP, LightGBM 走 CPU) ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model all --stocks 200 --epochs 50 --num-gpus 3

# ── 导出结果到 JSON ──
docker exec data-sync-trainer python /app/backend/train_cli.py \
  --model transformer --stocks 100 --epochs 20 --gpu 0 --output /app/backend/models/result.json
```

> **注意**：RL-PPO 内存开销大，`--epochs 150` 可能触发系统 OOM killer。建议 `--epochs 50`，分批训练。

**CLI 参数说明：**

| 参数 | 简写 | 默认值 | 说明 |
|---|---|---|---|
| `--model` | `-m` | 必填 | 模型类型: transformer / lstm / cnn_lstm / lightgbm / rl_ppo / all |
| `--stocks` | `-s` | 200 | 最大股票数量 |
| `--epochs` | `-e` | 50 | 训练轮数 |
| `--gpu` | `-g` | auto | 单 GPU 模式下的 GPU 编号，-1 强制 CPU |
| `--num-gpus` | `-n` | 1 | GPU 数量，>1 启用 DDP 分布式训练 |
| `--min-days` | — | 200 | 每只股票最少 K 线天数 |
| `--label-method` | — | zigzag | 标注方法 |
| `--pct-threshold` | — | 5.0 | 涨跌幅标注阈值 (%) |
| `--output` | `-o` | — | 将结果保存为 JSON 文件 |

## 🔧 DDP 分布式训练原理

```
submit_training(num_gpus=3)
    │
    ├── 1. 在主进程构建数据集 (一次性，避免 ORDER BY random() 导致各 worker 数据不一致)
    ├── 2. ray.put(dataset) → 放入 Ray Object Store
    ├── 3. TorchTrainer(scaling_config=3 workers, 1 GPU each)
    │
    └── 每个 Worker (rank 0/1/2):
         ├── ray.get(dataset_ref) → 取出相同数据
         ├── prepare_data_loader() → DistributedSampler 自动分片
         ├── prepare_model() → DistributedDataParallel 包装
         ├── 训练循环: forward → backward → NCCL all-reduce 梯度同步
         ├── rt.report() → 所有 rank 同步 barrier (每个 epoch)
         └── rank 0: 保存最优模型 + 汇报进度
```

**关键设计：**
- 数据集预构建：避免 `ORDER BY random()` 导致各 worker 数据不一致
- `drop_last=True`：保证所有 worker 的 batch 数量相同
- `rt.report()` 每个 epoch 所有 rank 都调用：避免 barrier 死锁
- 最终评估使用非分布式 DataLoader：确保指标准确

## 📡 主要 API

| 接口 | 说明 |
|---|---|
| `GET /api/market/overview` | 大盘指数与服务时间 |
| `GET /api/stocks/universe` | 标的列表 |
| `GET /api/stocks/{code}?lookback=120&sensitivity=5` | 行情 + K 线 + 自动画线结果 |
| `GET /api/screener/breakout_pullback` | 突破回踩形态扫描 |
| `GET /api/screener/stabilize` | 下跌企稳形态扫描 |
| `GET /api/screener/box_support` | 箱体支撑形态扫描 |
| `GET /api/screener/near_support` | 临近中强支撑扫描 |
| `GET /api/watchlist` / `POST` / `DELETE /{code}` | 自选股管理 |
| `POST /api/strategy/train_market` | 提交 AI 训练任务 |
| `GET /api/strategy/train_progress` | 查询训练进度 |

## ⚙️ 算法配置

`detect_levels()` 接受三个核心参数：

- `lookback`：回看周期（默认 120 日）
- `sensitivity`：极值灵敏度（`scipy.signal.argrelextrema` 的 `order`，默认 5）
- `cluster_tol_pct`：价位聚类容差（默认 1.2%）

形态评分均为 0-100，前端按 `≥80 高强 / ≥60 中等 / >0 弱` 三档展示。

## ⚖️ 合规声明

- 仅使用公开开源数据（akshare）
- 所有结果为**数据辅助**，**不构成任何投资建议**
- 不提供自动交易功能
