.PHONY: dev-backend dev-frontend install up down logs build backup restore

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
