# ========= 基本变量 =========
ENV ?= .env
COMPOSE ?= infra/docker-compose.dev.yml
DC = docker compose --env-file $(ENV) -f $(COMPOSE)

API_BASE ?= http://localhost:8000
API_PREFIX ?= /api/v1
API_URL = $(API_BASE)$(API_PREFIX)

# 把 .env 里的 KEY=VALUE 导出为当前 make 的环境变量（供 curl 用）
# 仅提取形如 VAR=xxx 的行；忽略注释/空行
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(ENV))

.PHONY: help up down restart rebuild ps config logs logs-api logs-worker logs-flower logs-cloudsql \
        health mvp db celery gcs check-proxy check-redis open-flower

help:
	@echo "常用命令："
	@echo "  make up            # 启动/更新所有服务（使用 $(COMPOSE) 和 $(ENV)）"
	@echo "  make down          # 停止并清理"
	@echo "  make ps            # 查看容器状态"
	@echo "  make logs          # 跟随所有服务日志（尾部 200 行）"
	@echo "  make logs-api      # 仅 API 日志"
	@echo "  make logs-worker   # 仅 Worker 日志"
	@echo "  make logs-flower   # 仅 Flower 日志"
	@echo "  make logs-cloudsql # 仅 Cloud SQL Proxy 日志"
	@echo "  make config        # 展开后的 compose 配置（用于检查变量插值是否正确）"
	@echo "  make health        # API 健康检查（/health）"
	@echo "  make mvp           # 访问 MVP 自测接口：$(API_BASE)/api/v1/mvp-test"
	@echo "  make db            # /debug/db  自检（建表/插入/查询）"
	@echo "  make celery        # /debug/celery 自检（入队一个任务）"
	@echo "  make gcs           # /debug/gcs 自检（写入并读回 GCS）"
	@echo "  make check-proxy   # 查看 Cloud SQL Proxy 最新日志片段"
	@echo "  make check-redis   # 对本地 Redis 做 PING"
	@echo "  make open-flower   # 打开 Flower UI 地址"

# ========= 容器生命周期 =========
up:
	$(DC) up -d --build

down:
	$(DC) down

restart: down up

rebuild:
	$(DC) build --no-cache

ps:
	$(DC) ps

# 展开后的配置，用于检查：CLOUD_SQL_CONN_NAME / CELERY_BROKER_URL 等是否已被插值
config:
	$(DC) config | sed -n '1,200p'

# ========= 日志快捷键 =========
logs:
	$(DC) logs -f --tail=200

logs-api:
	$(DC) logs -f api

logs-worker:
	$(DC) logs -f worker

logs-flower:
	$(DC) logs -f flower

logs-cloudsql:
	$(DC) logs -f cloudsql

# ========= 自检（需要 .env 里存在 SERVICE_API_KEY）=========
health:
	curl -fsSL -H "X-API-KEY: $(SERVICE_API_KEY)" $(API_URL)/health || true

# 你提供的 MVP 测试地址
mvp:
	curl -fsSL -H "X-API-KEY: $(SERVICE_API_KEY)" $(API_BASE)/api/v1/mvp-test || true

db:
	curl -fsSL -X POST -H "X-API-KEY: $(SERVICE_API_KEY)" $(API_URL)/debug/db || true

celery:
	curl -fsSL -X POST -H "X-API-KEY: $(SERVICE_API_KEY)" $(API_URL)/debug/celery || true

gcs:
	curl -fsSL -X POST -H "X-API-KEY: $(SERVICE_API_KEY)" $(API_URL)/debug/gcs || true

# ========= 辅助检查 =========
check-proxy:
	$(DC) logs cloudsql | tail -n 30

check-redis:
	$(DC) exec redis redis-cli PING || true

open-flower:
	@echo "Flower UI -> http://localhost:5555"
