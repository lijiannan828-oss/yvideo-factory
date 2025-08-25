SHELL := /bin/bash
.PHONY: up down logs build lint fmt test pre-commit

up:
	docker compose -f infra/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker-compose.yml down

logs:
	docker compose -f infra/docker-compose.yml logs -f --tail=200

build:
	docker compose -f infra/docker-compose.yml build

lint:
	ruff check . || true

fmt:
	ruff format . || true && black . || true

test:
	pytest -q || true

pre-commit:
	pre-commit install || true
