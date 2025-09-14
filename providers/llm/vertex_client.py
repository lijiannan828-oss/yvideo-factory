# -*- coding: utf-8 -*-
"""
Vertex AI Gemini 客户端最小封装（开发环境）
- 支持一次性输出(generate_once) 与 流式输出(generate_stream)
- 以环境变量读取：VERTEX_PROJECT / VERTEX_LOCATION / VERTEX_MODEL
- 本地开发通过 GOOGLE_APPLICATION_CREDENTIALS 使用 dev SA；生产走 ADC
"""
import os
from typing import Generator
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig


# 允许兼容你的变量命名：优先 VERTEX_*，否则回退 GCP_PROJECT_ID / GCP_REGION
VERTEX_PROJECT  = os.getenv("VERTEX_PROJECT") or os.getenv("GCP_PROJECT_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION") or os.getenv("GCP_REGION") or "us-central1"
VERTEX_MODEL_ID = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")


def _init_model() -> GenerativeModel:
    """初始化 Vertex 客户端并返回模型句柄"""
    if not VERTEX_PROJECT:
        raise ValueError("缺少 VERTEX_PROJECT 或 GCP_PROJECT_ID 环境变量")
    vertexai.init(project=VERTEX_PROJECT, location=VERTEX_LOCATION)
    return GenerativeModel(VERTEX_MODEL_ID)


def generate_once(prompt: str,
                  temperature: float = 0.4,
                  max_tokens: int = 60000,
                  as_json: bool = False) -> str:
    """
    一次性生成完整文本
    :param prompt: 提示词
    :param temperature: 发散度
    :param max_tokens: 最大输出 token
    :param as_json: True 时强制按照 JSON 文本返回（便于结构化落库）
    """
    model = _init_model()
    cfg = GenerationConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if as_json else None
    )
    resp = model.generate_content(prompt, generation_config=cfg)
    parts = []
    cand = resp.candidates[0]
    for p in cand.content.parts:
        if hasattr(p, "text") and p.text:
            parts.append(p.text)
    return "".join(parts).strip()


def generate_stream(prompt: str,
                    temperature: float = 0.6,
                    max_tokens: int = 60000) -> Generator[str, None, None]:
    """
    流式生成（服务端逐片返回），适合前端 SSE/WS
    :yield: 每个增量文本片段
    """
    model = _init_model()
    cfg = GenerationConfig(temperature=temperature, max_output_tokens=max_tokens)
    stream = model.generate_content(prompt, generation_config=cfg, stream=True)
    for chunk in stream:
        cand = chunk.candidates[0]
        for p in cand.content.parts:
            if hasattr(p, "text") and p.text:
                yield p.text
