<div align="center">

# PivotLab

**Open-source quantitative decision support terminal for A-share markets**

Auto support/resistance detection · Pattern screening · AI strategy training · Multi-GPU DDP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)

[Live Demo](https://lirixiang-pivotlab.hf.space/) · [Issues](https://github.com/lirixiang/pivotlab/issues) · [中文](README.md)

![PivotLab Screenshot](docs/screenshot.png)

</div>

---

## Features

| Feature | Description |
|---------|-------------|
| **Auto S/R Detection** | Identifies S1-S3 / R1-R3 support and resistance levels using local extrema + price clustering, with strength ratings and touch counts |
| **Pattern Screening** | Breakout pullback / stabilization / box support / near-support — 0-100 composite score per stock |
| **AI Strategy Training** | Transformer · LSTM · CNN-LSTM · LightGBM · RL-PPO, with single and multi-GPU DDP support |
| **LLM Agent** | Built-in conversational agent for querying market data and triggering scans via natural language |
| **Open Data** | Uses public APIs (akshare, Tencent Finance, East Money); falls back to deterministic mock data when offline |

## Tech Stack

**Backend** FastAPI · SQLAlchemy · Ray · PyTorch · akshare · APScheduler

**Frontend** React 18 · TypeScript · Vite · Tailwind CSS

**Database** PostgreSQL (production) / SQLite (lightweight, auto-selected)

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/lirixiang/pivotlab.git
cd pivotlab

cp .env.example .env
# Edit .env and fill in your API key

docker compose up -d
```

- Frontend: `http://localhost:7860`
- API: `http://localhost:18080/api/health`

### Option 2: Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
python run.py          # → http://localhost:8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev            # → http://localhost:5173
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```env
# LLM provider (qwen / deepseek / openai / siliconflow / doubao / glm)
LLM_DEFAULT_PROVIDER=qwen
LLM_DEFAULT_MODEL=qwen-turbo

QWEN_API_KEY=your_key_here
# DEEPSEEK_API_KEY=
# OPENAI_API_KEY=
```

If `DATABASE_URL` is not set, SQLite is used automatically — no database setup needed.

## Deployment

### Render (Free tier)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/lirixiang/pivotlab)

Or manually: create a **Blueprint** in Render dashboard, connect this repo — `render.yaml` automatically provisions the API service and PostgreSQL.

### Hugging Face Spaces

The repo includes `Dockerfile.spaces`. Create a Docker Space on HF and connect the repo. Uses SQLite (data resets on restart — suitable for demos).

## AI Strategy Training

Submit training jobs via the frontend Strategy page, or call the API directly:

```bash
# Single GPU training
curl -X POST http://localhost:18080/api/strategy/train_market \
  -H "Content-Type: application/json" \
  -d '{"model_type":"transformer","max_stocks":200,"epochs":50,"num_gpus":1}'

# Check progress
curl http://localhost:18080/api/strategy/train_progress
```

**Hardware requirements:** Minimum 1× NVIDIA GPU (≥8GB VRAM), CUDA 12.0+, `nvidia-container-toolkit`. Without GPU, LightGBM runs on CPU; other models are skipped.

## Key API Endpoints

Full docs available at `/docs` (Swagger UI).

| Endpoint | Description |
|----------|-------------|
| `GET /api/stocks/{code}` | Candlestick data + auto S/R detection results |
| `GET /api/screener/breakout_pullback` | Breakout pullback pattern scan |
| `GET /api/screener/near_support` | Near-support level scan |
| `POST /api/strategy/train_market` | Submit AI training job |
| `GET /api/agent/chat` | LLM Agent conversation |

## Contributing

PRs and Issues are welcome!

```bash
# After forking
git checkout -b feat/your-feature
# make your changes
git commit -m "feat: your feature description"
git push origin feat/your-feature
# Open a Pull Request
```

## Disclaimer

- Market data is sourced from public APIs (akshare, Tencent Finance, East Money) — all publicly available internet data, no non-public information involved
- All analysis results are **data-assisted references only** and **do not constitute investment advice**
- No automated trading functionality is provided

## License

[MIT](LICENSE) © 2025 lirixiang

Please retain the copyright notice when using this project's code.
