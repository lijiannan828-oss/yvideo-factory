# -*- coding: utf-8 -*-
"""
Vertex 调试路由
- /api/v1/vertex/generate  一次性生成，可写入 GCS
- /api/v1/vertex/stream    流式生成（SSE）
- /api/v1/vertex/task      异步生成，立即返回 task_id，结果写入 GCS
"""
from fastapi import APIRouter, Depends, Form
from fastapi.responses import StreamingResponse
from services.api.app.core.security import verify_api_key
from providers.llm.vertex_client import generate_once, generate_stream
from providers.storage.gcs_io import write_text
from workers.tasks.vertex_tasks import vertex_generate_and_store

router = APIRouter(prefix="/api/v1/vertex", tags=["vertex"])

@router.post("/generate")
def api_vertex_generate(prompt: str = Form(...),
                        to_gcs: bool = Form(True),
                        as_json: bool = Form(False),
                        _: None = Depends(verify_api_key)):
    """
    一次性生成：默认写入 GCS 的 test/ 目录
    """
    text = generate_once(prompt, as_json=as_json)
    gs_uri = None
    if to_gcs:
        gs_uri = write_text(text, suffix="json" if as_json else "txt")
    return {"prompt": prompt, "text": text, "gs_uri": gs_uri}

@router.post("/stream")
def api_vertex_stream(prompt: str = Form(...),
                      _: None = Depends(verify_api_key)):
    """
    流式生成：SSE（text/event-stream）
    - 前端可用 EventSource / fetch+ReadableStream 订阅
    """
    def _gen():
        for chunk in generate_stream(prompt):
            yield f"data: {chunk}\n\n"
        yield "event: done\ndata: [DONE]\n\n"
    return StreamingResponse(_gen(), media_type="text/event-stream")

@router.post("/task")
def api_vertex_task(prompt: str = Form(...),
                    as_json: bool = Form(False),
                    _: None = Depends(verify_api_key)):
    """
    异步：立即返回 task_id；任务在 Worker 执行并写入 GCS
    """
    r = vertex_generate_and_store.delay(prompt, as_json=as_json)
    return {"task_id": r.id}
