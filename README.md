---
title: PivotLab
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
tags:
  - finance
  - stock
  - trading
  - quantitative
  - a-share
  - technical-analysis
  - fastapi
  - react
---

<div align="center">

# PivotLab 智线

**面向 A 股的开源量化辅助决策终端**

自动支撑压力位识别 · 形态扫描 · AI 策略训练 · 多 GPU DDP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)

[演示 Demo](https://huggingface.co/spaces/lirixiang/pivotlab) · [API 文档](https://huggingface.co/spaces/lirixiang/pivotlab/api/health) · [提交 Issue](https://github.com/lirixiang/pivotlab/issues) · [English](README_EN.md)

![PivotLab 截图](docs/screenshot.png)

</div>

---

## 功能概览

| 功能 | 说明 |
|------|------|
| **自动画线** | 基于局部极值 + 价位聚类，识别 S1-S3 / R1-R3 多档位，附强度星级与触及次数 |
| **形态扫描** | 突破回踩 / 下跌企稳 / 箱体支撑 / 临近支撑，每股 0-100 综合评分 |
| **AI 训练** | Transformer · LSTM · CNN-LSTM · LightGBM · RL-PPO，支持多 GPU DDP |
| **Agent 对话** | 内置 LLM Agent，可通过自然语言查询行情、触发扫描 |
| **开源数据** | 使用 `akshare` 公开行情，离线自动回退 mock 数据 |

## 技术栈

**后端** FastAPI · SQLAlchemy · Ray · PyTorch · akshare · APScheduler

**前端** React 18 · TypeScript · Vite · Tailwind CSS

**数据库** PostgreSQL（生产）/ SQLite（轻量部署，自动切换）

## 快速开始

### 方式一：Docker（推荐）

```bash
git clone https://github.com/yourname/pivotlab.git
cd pivotlab

cp .env.example .env
# 编辑 .env 填入 API Key

docker compose up -d
```

- 前端：`http://localhost:9173`
- API：`http://localhost:18080/api/health`

### 方式二：本地开发

```bash
# 后端
cd backend
pip install -r requirements.txt
python run.py          # → http://localhost:8000

# 前端（新终端）
cd frontend
npm install
npm run dev            # → http://localhost:5173
```

## 配置

复制 `.env.example` 为 `.env`，按需填写：

```env
# LLM 提供商（qwen / deepseek / openai / siliconflow / doubao / glm）
LLM_DEFAULT_PROVIDER=qwen
LLM_DEFAULT_MODEL=qwen-turbo

QWEN_API_KEY=your_key_here
# DEEPSEEK_API_KEY=
# OPENAI_API_KEY=
```

不配置 `DATABASE_URL` 时默认使用 SQLite，无需额外安装数据库。

## 部署

### Render（免费）

点击一键部署：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/lirixiang/pivotlab)

或手动：在 Render 控制台选择 **Blueprint**，连接本仓库，`render.yaml` 会自动创建 API 服务 + PostgreSQL。

### Hugging Face Spaces

本仓库已包含 `Dockerfile.spaces`，直接在 HF Spaces 创建 Docker Space 并连接仓库即可。数据库使用 SQLite（重启后数据清空，适合演示）。

## AI 策略训练

通过前端「策略」页面提交训练任务，或直接调用 API：

```bash
# 单卡训练
curl -X POST http://localhost:18080/api/strategy/train_market \
  -H "Content-Type: application/json" \
  -d '{"model_type":"transformer","max_stocks":200,"epochs":50,"num_gpus":1}'

# 查看进度
curl http://localhost:18080/api/strategy/train_progress
```

**硬件要求：** 最低 1× NVIDIA GPU (≥8GB VRAM)，CUDA 12.0+，`nvidia-container-toolkit`。无 GPU 时 LightGBM 可在 CPU 运行，其余模型降级跳过。

## 主要 API

完整文档见 `/docs`（Swagger UI）。

| 接口 | 说明 |
|------|------|
| `GET /api/stocks/{code}` | K 线 + 自动画线结果 |
| `GET /api/screener/breakout_pullback` | 突破回踩形态扫描 |
| `GET /api/screener/near_support` | 临近支撑扫描 |
| `POST /api/strategy/train_market` | 提交 AI 训练任务 |
| `GET /api/agent/chat` | LLM Agent 对话 |

## 参与贡献

欢迎 PR 和 Issue！

```bash
# Fork 后
git checkout -b feat/your-feature
# ... 修改 ...
git commit -m "feat: your feature description"
git push origin feat/your-feature
# 提交 Pull Request
```

## 免责声明

- 仅使用 `akshare` 公开数据，不涉及任何非公开信息
- 所有分析结果为**数据辅助参考**，**不构成投资建议**
- 不提供自动交易功能

## 支持作者

如果这个项目对你有帮助，欢迎请我喝杯咖啡 ☕

<table>
  <tr>
    <td align="center"><b>支付宝</b></td>
    <td align="center"><b>微信</b></td>
  </tr>
  <tr>
    <td><img src="docs/alipay-qr.jpg" width="150"/></td>
    <td><img src="docs/wechat-qr.jpg" width="150"/></td>
  </tr>
</table>

## 联系作者

- 邮箱：[565539277@qq.com](mailto:565539277@qq.com)
- 微信：lx-ivan
- GitHub：[@lirixiang](https://github.com/lirixiang)

## License

[MIT](LICENSE) © 2025 lirixiang

使用本项目代码时请保留版权声明。
