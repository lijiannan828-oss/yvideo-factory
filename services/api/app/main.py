from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from services.api.app.api.v1.routes_chat import router as chat_router
from services.api.app.api.v1.routes_generate import router as generate_router
from services.api.app.api.v1.routes_json import router as json_router
from services.api.app.api.v1.routes_storyboardn import router as storyboardn_router
from services.api.app.api.v1.routes_stream import router as stream_router
from services.api.app.core.exceptions import register_exception_handlers
from services.api.app.core.security import verify_api_key

app = FastAPI(title="YWorkflow Storyboard API", version="1.3.0")

# CORS（按需收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(generate_router, dependencies=[Depends(verify_api_key)])
app.include_router(chat_router, dependencies=[Depends(verify_api_key)])
app.include_router(stream_router, dependencies=[Depends(verify_api_key)])
app.include_router(json_router, dependencies=[Depends(verify_api_key)])
app.include_router(storyboardn_router, dependencies=[Depends(verify_api_key)])

# 静态产物（round1/round2）
static_dir = Path("dev_outputs")
app.mount("/data", StaticFiles(directory=static_dir), name="data")

# 统一异常
register_exception_handlers(app)
