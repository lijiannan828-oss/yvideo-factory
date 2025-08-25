from fastapi import FastAPI, Depends
from services.api.app.core.security import verify_api_key
from services.api.app.core.exceptions import register_exception_handlers
from services.api.app.api.v1.routes_generate import router as generate_router
from services.api.app.api.v1.routes_chat import router as chat_router
from services.api.app.api.v1.routes_stream import router as stream_router
from services.api.app.api.v1.routes_json import router as json_router
#from app.api.routes_storyboard import router as storyboard_router
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="YWorkflow Storyboard API", version="1.3.0")

register_exception_handlers(app)

@app.get("/health", dependencies=[Depends(verify_api_key)])
def health():
    return {"status": "ok"}

# 如需版本前缀可加 prefix="/v1"
app.include_router(generate_router)
app.include_router(chat_router)
app.include_router(stream_router)
app.include_router(json_router)

#app.include_router(storyboard_router, tags=["storyboard"])


from services.api.app.api.v1.routes_storyboardn import router as storyboardn_router

# CORS（按需收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态目录：允许下载 /data/storyboard/...
DATA_DIR = (Path(__file__).resolve().parents[0] / "data").absolute()
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

# 路由
app.include_router(storyboardn_router)

"""
生产多 worker（推荐）：
  gunicorn -w 4 -k uvicorn.workers.UvicornWorker \
           --bind 0.0.0.0:${PORT:-8000} --timeout 120 app.main:app

建议：
- Round2 已做分批并发内部线程池；gunicorn worker 数量可 2~4 之间，根据 CPU 调整。
- Nginx 反代 → gunicorn(UvicornWorker, 多 worker) → 本应用。
"""
