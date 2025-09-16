# -*- coding: utf-8 -*-
"""
Vertex AI Gemini 客户端最小封装（新版 Google Gen AI SDK 版）
- 支持一次性输出(generate_once) 与 流式输出(generate_stream)
- 环境变量：VERTEX_PROJECT / VERTEX_LOCATION / VERTEX_MODEL
- 本地开发可用 GOOGLE_APPLICATION_CREDENTIALS；云上走 ADC
"""
import os
from typing import Generator

# ✅ 新版 SDK：google-genai
# 官方文档用法：from google import genai; client = genai.Client(...)
# 参考：client.models.generate_content / generate_content_stream
from google import genai
from google.genai import types

# 保持与原代码一致的变量名/回退逻辑
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT") or os.getenv("GCP_PROJECT_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION") or os.getenv("GCP_REGION") or "us-central1"
VERTEX_MODEL_ID = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")


def _init_model():
    """
    初始化环境并返回“模型句柄”
    【与原代码一致之处】
      - 仍然在此处校验 VERTEX_PROJECT
      - 仍然返回供后续生成调用使用的“句柄”（原来返回 aiplatform 的模型对象，
        现在返回新版 SDK 的 client 对象，充当等价“句柄”）
    【新版差异（等价替换）】
      - 原：aiplatform.init(...) + aiplatform.GenerativeModel(...)
      - 新：genai.Client(vertexai=True, project=..., location=...)
    """
    if not VERTEX_PROJECT:
        raise ValueError("缺少 VERTEX_PROJECT 或 GCP_PROJECT_ID 环境变量")

    # 按官方文档创建 Vertex 模式的客户端
    # （如需稳定版 API，可加 http_options=types.HttpOptions(api_version='v1')）
    client = genai.Client(
        vertexai=True,
        project=VERTEX_PROJECT,
        location=VERTEX_LOCATION,
    )
    return client  # 作为“句柄”返回（与原函数职责一致）


def generate_once(
    prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 60000,
    as_json: bool = False
) -> str:
    """
    一次性生成完整文本
    【与原代码一致之处】
      - 函数名/参数签名/默认值不变
      - 仍：获取句柄 → 组装生成配置 → 调用生成 → 优先返回 resp.text
    【新版等价实现】
      - 原：model.generate_content(..., generation_config=dict)
      - 新：client.models.generate_content(..., config=types.GenerateContentConfig)
    """
    client = _init_model()

    # 对应原来的 gen_config dict，这里改用官方的类型（字段语义等价）
    cfg = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        # 原逻辑：as_json=True 切到 JSON MIME；否则 text/plain
        response_mime_type="application/json" if as_json else "text/plain",
        # 如需更稳的结构化输出，可在此追加 response_schema=...（不改对外接口）
    )

    # 新版一次性生成：client.models.generate_content
    resp = client.models.generate_content(
        model=VERTEX_MODEL_ID,
        contents=prompt,
        config=cfg,
    )

    text = getattr(resp, "text", None)
    return text.strip() if isinstance(text, str) else str(resp)


def generate_stream(
    prompt: str,
    temperature: float = 0.6,
    max_tokens: int = 60000
) -> Generator[str, None, None]:
    """
    流式生成（服务端逐片返回）
    【与原代码一致之处】
      - 函数名/参数签名不变
      - 仍逐 chunk 产出 chunk.text
    【新版等价实现】
      - 原：model.generate_content(..., stream=True)
      - 新：client.models.generate_content_stream(...)
    """
    client = _init_model()

    cfg = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
    )

    # 官方文档：使用 generate_content_stream 获取同步流式增量
    for chunk in client.models.generate_content_stream(
        model=VERTEX_MODEL_ID,
        contents=prompt,
        config=cfg,
    ):
        text = getattr(chunk, "text", None)
        if isinstance(text, str) and text:
            yield text
