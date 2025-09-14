# -*- coding: utf-8 -*-
"""
GCS 文件写入/读取
- 开发环境：GOOGLE_APPLICATION_CREDENTIALS 指向 dev-sa-key.json
- 生产：Cloud Run/VM 走 ADC，无需设置该变量
"""
import os
import time
from google.cloud import storage

GCS_BUCKET = os.getenv("GCS_BUCKET_NAME")              # 例如：video_fatory-dev-bucket（若创建失败请改中划线）
GCS_PREFIX = os.getenv("GCS_OUTPUT_PREFIX", "test")    # 目录前缀，默认 test/

def _cli() -> storage.Client:
    return storage.Client()

def write_text(text: str, suffix: str = "txt") -> str:
    """
    把文本写入 gs://<bucket>/<GCS_PREFIX>/vertex_<ts>.<suffix>
    :return: gs:// URI
    """
    if not GCS_BUCKET:
        raise ValueError("缺少 GCS_BUCKET_NAME 环境变量")
    ts = int(time.time() * 1000)
    key = f"{GCS_PREFIX}/vertex_{ts}.{suffix}"
    blob = _cli().bucket(GCS_BUCKET).blob(key)
    content_type = "application/json" if suffix == "json" else "text/plain; charset=utf-8"
    blob.upload_from_string(text, content_type=content_type)
    return f"gs://{GCS_BUCKET}/{key}"

def read_text(gs_uri: str) -> str:
    assert gs_uri.startswith("gs://")
    _, bucket, key = gs_uri.split("/", 2)
    return _cli().bucket(bucket).blob(key).download_as_text()
