# services/api/app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# -----------------------------------------------------------------------------
# 核心模块导入 (Core Module Imports)
# -----------------------------------------------------------------------------
# 导入我们新创建的配置、数据库和API路由模块
# 这种相对导入方式是Python项目的最佳实践
from .core.config import settings
from .core.db import create_db_and_tables
from .api.v1 import (
    routes_storyboardn,
    routes_generate,
    routes_json,
    routes_stream,
    routes_chat,
    routes_mvp_test,
    routes_vertex
)

# -----------------------------------------------------------------------------
# FastAPI 生命周期事件 (Lifespan Events)
# -----------------------------------------------------------------------------
# 使用FastAPI推荐的lifespan上下文管理器来处理应用启动和关闭时的逻辑。
# 这比旧的 on_event 方式更现代化、更健壮。
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    在应用启动时运行的生命周期函数。
    """
    print("--- 应用启动 ---")
    # 调用db.py中的函数来创建数据库表（如果它们不存在）。
    # 这是我们实现服务启动时自动检查并准备数据库的关键一步。
    create_db_and_tables()
    print("--- 数据库表检查/创建完成 ---")
    yield
    # --- 在 'yield' 之后的代码会在应用关闭时运行 ---
    print("--- 应用关闭 ---")

# -----------------------------------------------------------------------------
# FastAPI 应用实例化 (App Instantiation)
# -----------------------------------------------------------------------------
# 创建FastAPI应用实例，并传入我们定义的生命周期函数。
app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan
)

# -----------------------------------------------------------------------------
# 中间件配置 (Middleware Configuration)
# -----------------------------------------------------------------------------
# 保留你原有的CORS（跨域资源共享）中间件配置。
# 关键修改：允许的源(origins)不再是硬编码的，而是从我们的config.py中的settings实例动态读取。
# 这使得我们在不同环境（开发、生产）下配置不同的CORS策略变得非常简单。
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# -----------------------------------------------------------------------------
# API 路由注册 (API Router Registration)
# -----------------------------------------------------------------------------
# 保留你所有现有的API路由，确保原有功能不受影响。
# 所有路由都统一加上了/api/v1的前缀，这是良好的版本管理习惯。
app.include_router(routes_storyboardn.router, prefix="/api/v1", tags=["storyboard"])
app.include_router(routes_generate.router, prefix="/api/v1", tags=["generate"])
app.include_router(routes_json.router, prefix="/api/v1", tags=["json"])
app.include_router(routes_stream.router, prefix="/api/v1", tags=["stream"])
app.include_router(routes_chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(routes_mvp_test.router, prefix="/api/v1", tags=["MVP Test"])
app.include_router(routes_vertex.router, prefix="/api/v1", tags=["vertex"])
# -----------------------------------------------------------------------------
# 根路由 / 健康检查 (Root Route / Health Check)
# -----------------------------------------------------------------------------
# 保留你原有的根路由，这通常用作一个简单的健康检查端点。
@app.get("/", tags=["Health Check"])
def read_root():
    """
    根路由，返回一个简单的欢迎信息，用于确认服务正在运行。
    """
    return {"message": f"Welcome to {settings.APP_NAME}!"}