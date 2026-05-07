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
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        nginx \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cu128 \
    && pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# Create NVIDIA lib directory and symlinks (actual .so files mounted at runtime)
RUN mkdir -p /usr/lib/nvidia \
    && ln -sf /usr/lib/nvidia/libcuda.so.570.172.08 /usr/lib/nvidia/libcuda.so.1 \
    && ln -sf /usr/lib/nvidia/libcuda.so.1 /usr/lib/nvidia/libcuda.so \
    && ln -sf /usr/lib/nvidia/libnvidia-ml.so.570.172.08 /usr/lib/nvidia/libnvidia-ml.so.1 \
    && ln -sf /usr/lib/nvidia/libnvidia-ptxjitcompiler.so.570.172.08 /usr/lib/nvidia/libnvidia-ptxjitcompiler.so.1 \
    && echo /usr/lib/nvidia > /etc/ld.so.conf.d/nvidia.conf && ldconfig

COPY backend ./backend
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-builder /build/frontend/dist /usr/share/nginx/html

RUN mkdir -p /app/backend/data
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default

COPY <<'EOF' /app/start.sh
#!/bin/sh
set -e

# Create NVIDIA symlinks and refresh ldconfig at runtime (libs mounted as volumes)
if [ -f /usr/lib/nvidia/libcuda.so.570.172.08 ]; then
  ln -sf /usr/lib/nvidia/libcuda.so.570.172.08 /usr/lib/nvidia/libcuda.so.1
  ln -sf /usr/lib/nvidia/libcuda.so.1 /usr/lib/nvidia/libcuda.so
  ln -sf /usr/lib/nvidia/libnvidia-ml.so.570.172.08 /usr/lib/nvidia/libnvidia-ml.so.1
  ln -sf /usr/lib/nvidia/libnvidia-ptxjitcompiler.so.570.172.08 /usr/lib/nvidia/libnvidia-ptxjitcompiler.so.1
  ldconfig
  echo "NVIDIA GPU libraries linked"
fi

uvicorn app.main:app --host 0.0.0.0 --port 18080 --workers 2 &
exec nginx -g 'daemon off;'
EOF

RUN chmod +x /app/start.sh

WORKDIR /app/backend

EXPOSE 80 18080
CMD ["/app/start.sh"]
