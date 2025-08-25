# -*- coding: utf-8 -*-
"""
Storyboard API 路由
- Round1：默认 2.5-pro 流式（失败 3 次 → 2.5-pro 非流式 3 次 → 降级），SSE 端点可见 chunk
- Round2：默认 batch 非流式，并发 4；失败链路：并发2 → 串行流式 → 串行非流式 → 降级
- 缺失镜头重生 3 轮，仍缺会在 meta 中列出 shot_id 与原因
- 统一落盘到 app/data/storyboard/YYYYMMDD，并返回可下载 URL
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from workers.llm.storyboard import (
    _extract_top_level_json,
    _model_repair_to_json_array,
    _parse_json_list_strict,
    generate_keyframe_prompts_batched,
    generate_pictures,
    generate_pictures_streaming_policy,
    new_run_id,
    persist_named_json,
    persist_named_text,
)

router = APIRouter(default_response_class=JSONResponse)

# 可选鉴权
_SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "").strip()


def _verify_api_key(x_api_key: Optional[str] = Header(None)):
    if _SERVICE_API_KEY and (not x_api_key or x_api_key != _SERVICE_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# ---------- 请求模型 ----------
class Round1Req(BaseModel):
    story: str
    style: Optional[str] = "cinematic, realistic"
    min_shots: int = Field(default=12, ge=1)
    max_shots: int = Field(default=500, ge=1)
    max_output_tokens: int = Field(default=50000, ge=4096)
    temperature: float = Field(default=0.5, ge=0.0, le=1.0)
    continue_segments: int = Field(default=6, ge=0, le=16)
    include_raw: bool = False


class Round2BatchedReq(BaseModel):
    pictures: List[Dict[str, Any]]
    characters: str
    scenes: str
    batch_size: int = Field(default=15, ge=1, le=50)
    max_output_tokens: int = Field(default=30000, ge=4096)
    temperature: float = Field(default=0.4, ge=0.0, le=1.0)
    continue_segments: int = Field(default=6, ge=0, le=16)
    max_missing_retry_rounds: int = Field(default=3, ge=0, le=6)
    parallel_workers: int = Field(default=4, ge=1, le=8)
    include_raw: bool = False


class FullPipelineReq(BaseModel):
    story: str
    style: Optional[str] = "cinematic, realistic"
    min_shots: int = 12
    max_shots: int = 500
    round1_max_output_tokens: int = 50000
    round1_temperature: float = 0.5
    round1_continue_segments: int = 6
    characters: str
    scenes: str
    batch_size: int = 15
    round2_max_output_tokens: int = 30000
    round2_temperature: float = 0.4
    round2_continue_segments: int = 6
    max_missing_retry_rounds: int = 3
    parallel_workers: int = 4


# ---------- Round1：非流式（兜底 / Full 使用） ----------
@router.post("/storyboardn/round1", dependencies=[Depends(_verify_api_key)])
def storyboardn_round1(req: Round1Req):
    pics_json, raw_text, meta = generate_pictures(
        story=req.story,
        style=req.style,
        min_shots=req.min_shots,
        max_shots=req.max_shots,
        max_output_tokens=req.max_output_tokens,
        temperature=req.temperature,
        continue_segments=req.continue_segments,
    )
    if not isinstance(pics_json, list) or len(pics_json) == 0:
        raise HTTPException(
            500, detail={"error": "round1_empty", "failures": meta.get("failures", [])}
        )
    stem = new_run_id()
    _, pics_url = persist_named_json(stem, "round1_pictures", pics_json)
    _, raw_url = persist_named_text(stem, "round1_raw", raw_text or "")
    return {
        "used_model": meta.get("used_model", ""),
        "failures": meta.get("failures", []),
        "shots": len(pics_json),
        "downloads": {
            "pictures_url": pics_url,
            "round1_raw_url": raw_url if req.include_raw else None,
        },
    }


# ---------- Round1：流式（2.5-pro×3 → 2.5-pro非流×3 → 降级） ----------
@router.post("/storyboardn/round1/stream", dependencies=[Depends(_verify_api_key)])
def storyboardn_round1_stream(req: Round1Req):
    stem = new_run_id()

    def event_stream():
        # 统一收集 raw；解析-修复-落盘-返回
        raw = ""
        gen, used_hint, fails = generate_pictures_streaming_policy(
            story=req.story,
            style=req.style,
            min_shots=req.min_shots,
            max_shots=req.max_shots,
            max_output_tokens=req.max_output_tokens,
            temperature=req.temperature,
        )
        for chunk in gen:
            if not chunk:
                continue
            raw += chunk
            yield f"event: chunk\ndata: {chunk}\n\n"

        pics = _parse_json_list_strict(raw) or _extract_top_level_json(raw) or []
        if not isinstance(pics, list):
            r = _model_repair_to_json_array(raw) or []
            pics = r if isinstance(r, list) else []
        if not pics:
            err = {"error": "round1_stream_policy_failed", "failures": fails}
            yield f"event: done\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"
            return

        _, pics_url = persist_named_json(stem, "round1_pictures", pics)
        _, raw_url = persist_named_text(stem, "round1_raw", raw or "")
        tail = {
            "used_model_hint": used_hint,
            "failures": fails,
            "shots": len(pics),
            "downloads": {
                "pictures_url": pics_url,
                "round1_raw_url": raw_url if req.include_raw else None,
            },
        }
        yield f"event: done\ndata: {json.dumps(tail, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------- Round2：并行策略（4→2→串行流式→串行非流式） ----------
@router.post("/storyboardn/round2/batched", dependencies=[Depends(_verify_api_key)])
def storyboardn_round2_batched(req: Round2BatchedReq):
    kf_json, merged_raw, meta = generate_keyframe_prompts_batched(
        pictures_json=req.pictures,
        characters=req.characters,
        scenes=req.scenes,
        batch_size=req.batch_size,
        max_output_tokens=req.max_output_tokens,
        temperature=req.temperature,
        continue_segments=req.continue_segments,
        max_missing_retry_rounds=req.max_missing_retry_rounds,
        parallel_workers=req.parallel_workers or 4,
    )
    if not isinstance(kf_json, list) or len(kf_json) == 0:
        raise HTTPException(
            500, detail={"error": "round2_empty", "failures": meta.get("failures", [])}
        )

    stem = new_run_id()
    _, kf_url = persist_named_json(stem, "round2_keyframes", kf_json)
    _, raw_url = persist_named_text(stem, "round2_raw", merged_raw or "")

    shots_input = len({s.get("shot_id") for s in req.pictures if isinstance(s, dict)})
    shots_covered = len({f.get("shot_id") for f in kf_json})

    resp = {
        "used_model": meta.get("used_model", ""),
        "failures": meta.get("failures", []),
        "frames": len(kf_json),
        "meta": {
            "batches": meta.get("batches", 0),
            "retry_rounds": meta.get("retry_rounds", 0),
            "shots_input": shots_input,
            "shots_covered": shots_covered,
        },
        "downloads": {
            "keyframes_url": kf_url,
            "round2_raw_url": raw_url if req.include_raw else None,
        },
    }
    # 3) 如果缺失镜头仍存在，附带提醒
    missing = meta.get("missing_after_retries", [])
    if missing:
        resp["missing_after_retries"] = missing
        resp["missing_reasons"] = meta.get("missing_reasons", {})
        resp["message"] = f"{len(missing)} shot(s) failed after retries: {missing}"
    return resp


# ---------- Full：R1(非流式) + R2（策略 batched） ----------
@router.post("/storyboardn/full", dependencies=[Depends(_verify_api_key)])
def storyboardn_full(req: FullPipelineReq):
    # Round1（非流式兜底，full 不走流式）
    pics_json, pics_raw, meta1 = generate_pictures(
        story=req.story,
        style=req.style,
        min_shots=req.min_shots,
        max_shots=req.max_shots,
        max_output_tokens=req.round1_max_output_tokens,
        temperature=req.round1_temperature,
        continue_segments=req.round1_continue_segments,
    )
    if not isinstance(pics_json, list) or len(pics_json) == 0:
        raise HTTPException(
            500, detail={"error": "round1_empty", "failures": meta1.get("failures", [])}
        )

    # Round2 策略 batched
    kf_json, kf_raw, meta2 = generate_keyframe_prompts_batched(
        pictures_json=pics_json,
        characters=req.characters,
        scenes=req.scenes,
        batch_size=req.batch_size,
        max_output_tokens=req.round2_max_output_tokens,
        temperature=req.round2_temperature,
        continue_segments=req.round2_continue_segments,
        max_missing_retry_rounds=req.max_missing_retry_rounds,
        parallel_workers=req.parallel_workers or 4,
    )
    if not isinstance(kf_json, list) or len(kf_json) == 0:
        raise HTTPException(
            500, detail={"error": "round2_empty", "failures": meta2.get("failures", [])}
        )

    stem = new_run_id()
    _, pics_url = persist_named_json(stem, "round1_pictures", pics_json)
    _, raw1_url = persist_named_text(stem, "round1_raw", pics_raw or "")
    _, kf_url = persist_named_json(stem, "round2_keyframes", kf_json)
    _, raw2_url = persist_named_text(stem, "round2_raw", kf_raw or "")

    package = {
        "id": stem,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "round1": {
            "used_model": meta1.get("used_model", ""),
            "failures": meta1.get("failures", []),
            "shots": len(pics_json),
        },
        "round2": {
            "used_model": meta2.get("used_model", ""),
            "failures": meta2.get("failures", []),
            "frames": len(kf_json),
        },
        "downloads": {
            "pictures_url": pics_url,
            "keyframes_url": kf_url,
            "round1_raw_url": raw1_url,
            "round2_raw_url": raw2_url,
        },
    }
    # 缺失镜头提醒
    if meta2.get("missing_after_retries"):
        package["round2"]["missing_after_retries"] = meta2["missing_after_retries"]
        package["round2"]["missing_reasons"] = meta2.get("missing_reasons", {})

    _, pkg_url = persist_named_json(stem, "package_meta", package)

    return {
        "round1": {
            "used_model": meta1.get("used_model", ""),
            "failures": meta1.get("failures", []),
            "shots": len(pics_json),
        },
        "round2": {
            "used_model": meta2.get("used_model", ""),
            "failures": meta2.get("failures", []),
            "frames": len(kf_json),
            "missing_after_retries": meta2.get("missing_after_retries", []),
        },
        "downloads": {"pictures_url": pics_url, "keyframes_url": kf_url, "package_url": pkg_url},
    }
