SHELL := /bin/bash

# =========================
# 基础配置（按需修改）
# =========================
COMPOSE_FILE ?= infra/docker-compose.yml
RUN          := docker compose -f $(COMPOSE_FILE)
EXEC_API     := docker compose -f $(COMPOSE_FILE) exec api sh -lc

# 镜像仓库（GHCR / Docker Hub 均可）
REGISTRY ?= ghcr.io
OWNER    ?= <你的GitHub用户名或组织>   # 例如: lijiannan828-oss
APP      ?= yvideo-factory
TAG      ?= $(shell git rev-parse --short HEAD)
IMAGE    ?= $(REGISTRY)/$(OWNER)/$(APP):$(TAG)

# 远程服务器（镜像拉取部署）
DEPLOY_HOST ?= ubuntu@your.server.com
DEPLOY_DIR  ?= /opt/yvideo-factory

# =========================
# 帮助
# =========================
.PHONY: help
help:
	@echo "可用目标："
	@echo "  make up            - 启动开发环境（compose up -d --build）"
	@echo "  make down          - 关闭开发环境"
	@echo "  make logs          - 跟随查看日志"
	@echo "  make ps            - 查看容器状态"
	@echo "  make restart       - 重启 api 容器"
	@echo "  make rebuild       - 强制重建（--no-cache）并启动"
	@echo "  make shell         - 进入 api 容器 shell"
	@echo "  make dev-setup     - 在 api 容器内安装 ruff/black/pytest（一次即可）"
	@echo "  make fmt           - 代码格式化（容器内：ruff format + black）"
	@echo "  make lint          - 代码静态检查（容器内：ruff）"
	@echo "  make test          - 运行测试（容器内：pytest）"
	@echo "  make check         - lint + test"
	@echo "  make image         - 本地构建 amd64 镜像"
	@echo "  make pushimage     - 构建并推送镜像到仓库"
	@echo "  make tag_latest    - 同步推送 latest 标签"
	@echo "  make deploy TAG=sha - 到服务器拉取镜像并启动（使用 .env.prod）"
	@echo "  make smoke         - 运行本地冒烟脚本 scripts/validate_storyboard.sh"
	@echo ""

# =========================
# 开发环境（Compose）
# =========================
.PHONY: up down logs ps restart rebuild shell
up:
	$(RUN) up -d --build

down:
	$(RUN) down

logs:
	$(RUN) logs -f --tail=200

ps:
	$(RUN) ps

restart:
	$(RUN) restart api

rebuild:
	$(RUN) build --no-cache
	$(RUN) up -d

shell:
	$(EXEC_API) 'bash || sh'

# =========================
# 开发工具（容器内执行）
# =========================
.PHONY: dev-setup fmt lint test check
dev-setup:
	$(EXEC_API) 'python -m pip install -q "ruff==0.5.6" "black==24.4.2" "pytest==8.3.2" "pre-commit==3.7.1" && python -m pip list | grep -E "ruff|black|pytest|pre-commit" || true'

fmt:
	$(EXEC_API) 'python -m ruff format . && python -m black .'

lint:
	$(EXEC_API) 'python -m ruff check .'

test:
	$(EXEC_API) 'python -m pytest -q'

check: lint test

.PHONY: autofix
autofix:
	$(EXEC_API) 'python -m ruff check . --fix --unsafe-fixes && python -m ruff format . && python -m black .'


# =========================
# 镜像构建 / 推送（amd64）
# =========================
.PHONY: image pushimage tag_latest
image:
	docker buildx build --platform linux/amd64 -f Dockerfile.api -t $(IMAGE) .

pushimage:
	docker buildx build --platform linux/amd64 -f Dockerfile.api -t $(IMAGE) --push .

tag_latest:
	docker buildx build --platform linux/amd64 -f Dockerfile.api -t $(REGISTRY)/$(OWNER)/$(APP):latest --push .

# =========================
# 部署（服务器拉镜像）
# 服务器上需存在：
#   /opt/yvideo-factory/.env.prod （含 SERVICE_API_KEY/GOOGLE_API_KEY 等）
#   /opt/yvideo-factory/infra/docker-compose.prod.yml
# =========================
.PHONY: deploy
deploy:
	@test -n "$(TAG)" || (echo "请提供 TAG，例如: make deploy TAG=$$(git rev-parse --short HEAD)"; exit 1)
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && \
	export IMAGE_TAG=$(TAG) && \
	docker compose -f infra/docker-compose.prod.yml --env-file .env.prod pull && \
	docker compose -f infra/docker-compose.prod.yml --env-file .env.prod up -d && \
	docker compose -f infra/docker-compose.prod.yml --env-file .env.prod ps"

# =========================
# 冒烟（本地）
# =========================
.PHONY: smoke
smoke:
	@test -x scripts/validate_storyboard.sh || (echo "❌ 缺少 scripts/validate_storyboard.sh 或未加可执行权限"; exit 1)
	bash scripts/validate_storyboard.sh
