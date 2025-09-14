# -*- coding: utf-8 -*-
"""
Celery 异步任务：调用 Vertex -> 写入 GCS
"""
import json
from services.api.app.core.celery_app import celery_app  # 注意：你的 Celery 实例路径
from providers.llm.vertex_client import generate_once
from providers.storage.gcs_io import write_text

@celery_app.task(
        name="vertex.generate_and_store",
        queue="default",                # 指定队列
        routing_key="task.default"  )   # 指定路由键
def vertex_generate_and_store(prompt: str,
                              temperature: float = 0.4,
                              max_tokens: int = 60000,
                              as_json: bool = False) -> dict:
    """
    异步：一次性生成 -> 写 GCS
    :return: {"gs_uri": "...", "length": 123}
    """
    text = generate_once(prompt, temperature=temperature, max_tokens=max_tokens, as_json=as_json)
    payload = json.dumps({"prompt": prompt, "text": text}, ensure_ascii=False)
    gs_uri = write_text(payload, suffix="json")
    return {"gs_uri": gs_uri, "length": len(text)}
