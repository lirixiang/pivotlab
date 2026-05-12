.PHONY: dev-backend dev-frontend install up down logs build backup restore frontend-deploy backend-reload

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

dev-backend:
	cd backend && python run.py

dev-frontend:
	cd frontend && npm run dev

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

backup:
	bash backup/backup.sh

restore:
	bash backup/restore.sh

# Build the frontend locally and push the new dist into the running container's
# nginx webroot. Avoids a full image rebuild during iteration.
# Usage: make frontend-deploy [CONTAINER=data-sync-worker]
CONTAINER ?= data-sync-worker
frontend-deploy:
	cd frontend && npm run build
	docker cp frontend/dist/. $(CONTAINER):/usr/share/nginx/html/
	@echo "✔ frontend deployed to $(CONTAINER):/usr/share/nginx/html/  (hard-refresh browser: Ctrl+Shift+R)"

# Restart the FastAPI app inside the container (picks up backend code changes
# from the bind-mounted ./backend volume).
backend-reload:
	docker-compose restart app
	@echo "✔ backend restarted"

