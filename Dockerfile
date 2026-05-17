ARG DOCKERHUB_LIBRARY_MIRROR=m.daocloud.io/docker.io/library

FROM ${DOCKERHUB_LIBRARY_MIRROR}/node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json ./package.json
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY frontend ./
RUN npm run build

FROM ${DOCKERHUB_LIBRARY_MIRROR}/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        nginx \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip \
    # && pip install torch --index-url https://download.pytorch.org/whl/cu128 \
    && pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY backend ./backend
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-builder /build/frontend/dist /usr/share/nginx/html

RUN mkdir -p /app/backend/data /workspace
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default

COPY <<'EOF' /app/start.sh
#!/bin/sh
set -e
uvicorn app.main:app --host 0.0.0.0 --port 18080 --workers 4 &
exec nginx -g 'daemon off;'
EOF

RUN chmod +x /app/start.sh

WORKDIR /app/backend

EXPOSE 9173 18080
CMD ["/app/start.sh"]
