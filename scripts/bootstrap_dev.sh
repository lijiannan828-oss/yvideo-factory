#!/usr/bin/env bash
set -euo pipefail

# 1) env
[[ -f .env ]] || cp .env.example .env
echo "[ok] .env ready"

# 2) deps (local optional)
pip install -r services/api/requirements.txt || true

# 3) pre-commit (optional)
pre-commit install || true

# 4) docker compose up
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml ps

