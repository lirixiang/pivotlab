FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        nginx \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-demo.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend ./backend
COPY --from=frontend-builder /build/frontend/dist /usr/share/nginx/html

# HF Spaces runs on port 7860
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
  listen 7860;
  server_name _;
  root /usr/share/nginx/html;
  index index.html;

  # HF proxies the public site over 443; nginx listens on 7860. Without this,
  # `return 302 /agent` emits an absolute Location with :7860, unreachable publicly.
  absolute_redirect off;
  port_in_redirect off;

  location = / {
    return 302 /agent;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:18080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 300s;
    proxy_buffering off;
    proxy_cache off;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
    chunked_transfer_encoding off;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
EOF

RUN mkdir -p /app/backend/data /workspace \
    && rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default \
    && rm -f /etc/nginx/conf.d/default.conf.dpkg-dist 2>/dev/null || true

COPY <<'EOF' /app/start.sh
#!/bin/sh
set -e
uvicorn app.main:app --host 0.0.0.0 --port 18080 --workers 2 &
exec nginx -g 'daemon off;'
EOF

RUN chmod +x /app/start.sh

WORKDIR /app/backend

EXPOSE 7860
CMD ["/app/start.sh"]
