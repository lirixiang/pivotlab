# PivotLab 智线

> **自动支撑压力位 · 形态筛选 · 入场信号** —— 面向 A 股技术分析散户的开源辅助决策终端。

## ✨ 核心能力

- **自动画线**：基于 `scipy` 局部极值 + 价位聚类 + 均线动态，自动识别 R1/R2/R3 / S1/S2/S3 多档支撑压力位，附强度星级（1-5）和触及次数。
- **形态筛选**：内置「**突破回踩**」「**下跌企稳**」两大量化形态扫描器，每只股票计算 0-100 综合评分（突破力度 / 回踩缩量 / 压力强度 / 多周期共振）。
- **专业终端 UI**：深色三栏布局（自选 + 主图 + 信号面板），金色压力位 / 天青蓝支撑位双色体系，对标 Bloomberg / TradingView 视觉规范。
- **开源数据**：使用 `akshare` 公开行情；网络不可用时自动回退到确定性 mock 数据，保证开发与演示永远可跑。

## 🏗️ 架构

```
pivotlab/
├── backend/                FastAPI + scipy + akshare
│   └── app/
│       ├── routers/        market / stocks / screener / watchlist
│       ├── services/
│       │   ├── data_provider.py  akshare 适配 + mock 回退
│       │   ├── levels.py          支撑压力位识别算法
│       │   └── screener.py        形态扫描器
│       └── main.py
├── frontend/               Vite + React 18 + TS + Tailwind 3
│   └── src/
│       ├── components/     TopBar / Watchlist / Chart / Signal / Screener
│       ├── services/api.ts
│       └── App.tsx
├── prototype/              静态 HTML 原型（设计基线）
├── Dockerfile              单镜像：前端构建 + nginx + FastAPI
├── docker-compose.yml      单容器运行
└── Makefile                常用命令
```

## 🚀 快速开始

### 本地开发（推荐）

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

### Docker 一键启动

```bash
make up                  # 前端+反代 :5173 / 直连后端 :8001
make logs
make down
```

Docker 模式下只启动一个容器：

- `http://localhost:5173`：前端页面，同时通过 nginx 反代 `/api`
- `http://localhost:8001/api/health`：直连 FastAPI，便于调试接口

## 📡 主要 API

| 接口 | 说明 |
|---|---|
| `GET /api/market/overview` | 大盘指数与服务时间 |
| `GET /api/stocks/universe` | 标的列表 |
| `GET /api/stocks/{code}?lookback=120&sensitivity=5` | 行情 + K 线 + 自动画线结果 |
| `GET /api/screener/breakout_pullback` | 突破回踩形态扫描 |
| `GET /api/screener/bottom_stabilize` | 下跌企稳形态扫描 |
| `GET /api/watchlist` / `POST` / `DELETE /{code}` | 自选股管理 |

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
