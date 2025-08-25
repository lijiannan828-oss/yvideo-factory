# -*- coding: utf-8 -*-
"""
StoryBoard 角色层（Round1 + Round2）
满足的新策略：
1) Round1 默认 2.5-pro 流式；失败重试 3 次；仍失败 → 2.5-pro 非流式重试 3 次；再失败 → 降级（候选列表）
2) Round2 默认 batch 非流式，并发 4；若并发失败 → 并发 2；若仍失败 → 串行“流式”；若仍失败 → 串行“非流式”；再失败 → 降级（候选列表）
3) 缺失镜头重生 3 轮，仍缺 → 在返回 meta 中明确列出缺失 shot_id 和原因
4) 其他原有的稳健性逻辑保留：JSON 解析修复、续写一致性、排序对齐、占位补齐、落盘/下载 URL
"""

from __future__ import annotations
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Iterable, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from providers.llm.gemini import GeminiClient

# ---------- 目录 & 下载 URL ----------
REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT / 'services' / 'api' / 'app'
PROMPTS_DIR = (REPO_ROOT / 'prompts' / 'storyboard_artist')
DATA_ROOT   = (REPO_ROOT / 'services' / 'api' / 'app' / 'data' / 'storyboard')
DATA_ROOT.mkdir(parents=True, exist_ok=True)
DOWNLOAD_BASE = "/data/storyboard"  # 由 main.py 挂静态目录

def _today_dir() -> Path:
    d = DATA_ROOT / datetime.now().strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    return d

def _today_url_prefix() -> str:
    return f"{DOWNLOAD_BASE}/{datetime.now().strftime('%Y%m%d')}"

def new_run_id() -> str:
    return uuid.uuid4().hex

def persist_named_json(stem: str, kind: str, obj: Any) -> Tuple[str, str]:
    outdir = _today_dir()
    p = outdir / f"{stem}_{kind}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p), f"{_today_url_prefix()}/{p.name}"

def persist_named_text(stem: str, kind: str, text: str) -> Tuple[str, str]:
    outdir = _today_dir()
    p = outdir / f"{stem}_{kind}.txt"
    p.write_text(text or "", encoding="utf-8")
    return str(p), f"{_today_url_prefix()}/{p.name}"

# ---------- Gemini 单例（Pro 优先 + 续写） ----------
_client = GeminiClient(
    model_candidates=[
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash",
        "models/gemini-1.5-pro-latest",
    ],
    on_max_tokens="continue",
    max_continue_segments=10,
    continue_prompt=(
        "继续上文，从中断处严格续写；禁止重复任何已输出文本；"
        "若正在输出 JSON 数组：只能追加新元素，延续编号，不要重开或重写旧元素；"
        "除非内容完结，不要闭合数组；禁止解释/前情回顾/格式变更。"
    ),
    default_generation_config={
        "temperature": 0.5,
        "top_p": 0.95,
        "max_output_tokens": 50000,
    },
)

# ---------- 模板 ----------
_DA_PATTERN = re.compile(r"<<\s*([a-zA-Z0-9_]+)\s*>>")

def _render(tmpl: str, mapping: Dict[str, Any]) -> str:
    def _repl(m: re.Match):
        k = m.group(1)
        return str(mapping.get(k, m.group(0)))
    return _DA_PATTERN.sub(_repl, tmpl)

def load_prompt_text(relpath: str) -> str:
    p = (PROMPTS_DIR / relpath.strip("/")).resolve()
    if not p.exists():
        raise FileNotFoundError(relpath)
    return p.read_text(encoding="utf-8")

# ---------- JSON 解析 & 修复 ----------
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)

def _strip_code_fences(s: str) -> str:
    m = _CODE_FENCE.search(s or "")
    return m.group(1).strip() if m else (s or "").strip()

def _extract_top_level_json(s: str) -> Optional[Any]:
    if not s:
        return None
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    for left, right in (("[", "]"), ("{", "}")):
        try:
            li = s.index(left)
            ri = s.rfind(right)
            if ri > li:
                return json.loads(s[li:ri+1])
        except Exception:
            continue
    return None

def _json_sanitize_minimal(s: str) -> str:
    s = _strip_code_fences(s)
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = re.sub(r",(\s*[\]\}])", r"\1", s)
    return s

def _parse_json_list_strict(s: str) -> Optional[List[Any]]:
    if not s:
        return None
    s1 = _json_sanitize_minimal(s)
    try:
        obj = json.loads(s1)
        return obj if isinstance(obj, list) else None
    except Exception:
        pass
    try:
        li = s1.index("["); ri = s1.rfind("]")
        if ri > li:
            obj = json.loads(s1[li:ri+1])
            return obj if isinstance(obj, list) else None
    except Exception:
        return None
    return None

def _model_repair_to_json_array(raw_text: str) -> Optional[List[Any]]:
    repair_prompt = (
        "下面是一段应为 JSON 数组的内容，但可能被包裹在说明/Markdown中或存在格式问题。\n"
        "请将其修复为严格合法的 JSON 数组（仅输出 JSON，无任何解释/Markdown）：\n\n"
        f"{raw_text}"
    )
    text, _, _ = _client.generate_with_fallback(
        repair_prompt, temperature=0.0, max_output_tokens=12000,
        on_max_tokens="return", response_mime_type="application/json",
    )
    return _parse_json_list_strict(text or "")

# ---------- Round1（策略版） ----------
def _try_stream_pro_3x(prompt: str, **kwargs) -> Tuple[Optional[Iterable[str]], List[str]]:
    fails: List[str] = []
    orig = list(_client.model_candidates)
    try:
        _client.model_candidates = ["models/gemini-2.5-pro"]
        for i in range(3):
            try:
                gen, used, f = _client.stream_with_fallback(prompt, **kwargs)
                fails.extend(f or [])
                return gen, fails
            except Exception as e:
                fails.append(f"round1_pro_stream_attempt#{i+1}: {e}")
        return None, fails
    finally:
        _client.model_candidates = orig

def _try_nonstream_pro_3x(prompt: str, **kwargs) -> Tuple[Optional[str], List[str]]:
    fails: List[str] = []
    orig = list(_client.model_candidates)
    try:
        _client.model_candidates = ["models/gemini-2.5-pro"]
        for i in range(3):
            try:
                txt, used, f = _client.generate_with_fallback(prompt, **kwargs)
                fails.extend(f or [])
                if (txt or "").strip():
                    return txt, fails
            except Exception as e:
                fails.append(f"round1_pro_nonstream_attempt#{i+1}: {e}")
        return None, fails
    finally:
        _client.model_candidates = orig

def generate_pictures_streaming_policy(
    story: str,
    *,
    style: str = "cinematic, realistic",
    min_shots: int = 12,
    max_shots: int = 500,
    max_output_tokens: int = 50000,
    temperature: float = 0.5,
) -> Tuple[Iterable[str], str, List[str]]:
    """
    Round1（流式端点使用）：
      pro 流式(×3) → 失败则 pro 非流式(×3) → 仍失败则降级（非流式）
    返回：(chunk 迭代器, used_model_hint, failures)
    """
    tmpl = load_prompt_text("round1_pictures.txt")
    prompt = _render(tmpl, {
        "story": story, "style": style or "cinematic, realistic",
        "min_shots": min_shots, "max_shots": max_shots,
    }) + "\n\n【输出要求】仅输出一个严格合法的 JSON 数组；不要包含任何解释、Markdown 或注释。"

    kwargs = {
        "temperature": float(temperature),
        "max_output_tokens": int(max_output_tokens),
        "on_max_tokens": "continue",
        "response_mime_type": "application/json",
    }

    # 1) pro 流式 ×3
    gen, fails = _try_stream_pro_3x(prompt, **kwargs)
    if gen is not None:
        return gen, "models/gemini-2.5-pro", fails

    # 2) pro 非流式 ×3
    txt, fails2 = _try_nonstream_pro_3x(prompt, **kwargs)
    fails.extend(fails2)
    if txt and txt.strip():
        def single_iter():
            yield txt
        return single_iter(), "models/gemini-2.5-pro", fails

    # 3) 降级（非流式）
    text, used, f3 = _client.generate_with_fallback(prompt, **kwargs)
    fails.extend(f3 or [])
    def single_iter2():
        yield text or ""
    return single_iter2(), used or "", fails

def generate_pictures(
    story: str,
    *,
    style: str = "cinematic, realistic",
    min_shots: int = 12,
    max_shots: int = 500,
    max_output_tokens: int = 50000,
    temperature: float = 0.5,
    continue_segments: Optional[int] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], str, Dict[str, Any]]:
    """Round1 非流式（用于 full/兜底），按候选降级（内部自带续写）"""
    tmpl = load_prompt_text("round1_pictures.txt")
    prompt = _render(tmpl, {
        "story": story, "style": style or "cinematic, realistic",
        "min_shots": min_shots, "max_shots": max_shots,
    }) + "\n\n【输出要求】仅输出一个严格合法的 JSON 数组；不要包含任何解释、Markdown 或注释。"

    kwargs = {
        "temperature": float(temperature),
        "max_output_tokens": int(max_output_tokens),
        "on_max_tokens": "continue",
        "response_mime_type": "application/json",
    }
    if continue_segments is not None:
        kwargs["continue_segments"] = int(continue_segments)

    text, used, fails = _client.generate_with_fallback(prompt, **kwargs)
    obj = _parse_json_list_strict(text) or _extract_top_level_json(text or "")
    if obj is None:
        obj = _model_repair_to_json_array(text or "")
    return obj, (text or ""), {"used_model": used, "failures": fails}

# ---------- Round2 工具 ----------
def _chunk_list(items: List[Any], n: int) -> List[List[Any]]:
    return [items[i:i+n] for i in range(0, len(items), n)]

def _shot_order_map(pictures_json: List[Dict[str, Any]]) -> Dict[str, int]:
    order = {}
    for idx, shot in enumerate(pictures_json):
        sid = str(shot.get("shot_id") or f"S{idx+1:03d}")
        if sid not in order:
            order[sid] = len(order)
    return order

def _normalize_keyframe(kf: Dict[str, Any]) -> Dict[str, Any]:
    defaults = {
        "frame_idx": 1, "frame": "", "style": "写实电影感, 8K画质",
        "isMaincharacter": 0, "charactId": "", "isMainScene": 0, "sceneId": "",
        "text": "", "sfx": [], "prompt": "",
        "nprompt": "blurry, low quality, worst quality, jpeg artifacts, signature, watermark",
        "promptv": "", "seed": 19930711,
    }
    out = defaults | kf
    try:
        out["frame_idx"] = int(out.get("frame_idx") or 1)
    except Exception:
        out["frame_idx"] = 1
    return out

def _placeholder_from_shot(shot: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(shot.get("shot_id", "SXXX"))
    return _normalize_keyframe({
        "shot_id": sid,
        "frame_idx": 1,
        "frame": "【占位】关键帧生成失败，待补充。",
        "prompt": "placeholder frame; do not use for final rendering",
        "promptv": shot.get("promptv", shot.get("action", "")),
        "seed": shot.get("seed", 19930711),
    })

def _render_round2_prompt(batch_items: List[Dict[str, Any]], characters: str, scenes: str) -> str:
    tmpl = load_prompt_text("round2_keyframes.txt")
    snippet = json.dumps(batch_items, ensure_ascii=False, indent=2)
    prompt = _render(tmpl, {
        "pictures_json_snippet": snippet,
        "characters": characters or "",
        "scenes": scenes or "",
    })
    prompt += (
        "\n\n【输出要求】仅输出一个严格合法的 JSON 数组；不要包含任何解释、注释、Markdown。\n"
        "【覆盖性约束】必须覆盖本批次 *所有* shot_id，每个镜头至少 1 个关键帧；"
        "frame_idx 从 1 递增；JSON 数组顺序与输入镜头顺序一致。"
    )
    return prompt

def _sort_key_for(order_map: Dict[str, int], kf: Dict[str, Any]) -> Tuple[int, int]:
    sid = str(kf.get("shot_id"))
    return (order_map.get(sid, 10**9), int(kf.get("frame_idx") or 1))

def _run_one_batch(
    prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    continue_segments: int,
    prefer_stream: bool = False,
    on_stream: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, Any]], str, List[str], Optional[str]]:
    """执行一个 batch；prefer_stream=True 则先流式，否则非流式；内部仍有模型降级/续写。"""
    local_fails: List[str] = []
    used: Optional[str] = None
    raw = ""

    if prefer_stream and on_stream:
        try:
            gen, used0, f0 = _client.stream_with_fallback(
                prompt,
                temperature=float(temperature),
                max_output_tokens=int(max_output_tokens),
                on_max_tokens="continue",
                response_mime_type="application/json",
                continue_segments=int(continue_segments),
            )
            used = used0
            for chunk in gen:
                if not chunk:
                    continue
                raw += chunk
                on_stream(chunk)
            if f0:
                local_fails.extend(f0)
        except Exception as e:
            local_fails.append(f"stream_fail: {e}")

    if not raw:  # 非流式兜底
        text, used0, f0 = _client.generate_with_fallback(
            prompt,
            temperature=float(temperature),
            max_output_tokens=int(max_output_tokens),
            on_max_tokens="continue",
            response_mime_type="application/json",
            continue_segments=int(continue_segments),
        )
        raw = text or ""
        used = used or used0
        if f0:
            local_fails.extend(f0)

    parsed: List[Dict[str, Any]] = _parse_json_list_strict(raw) or _extract_top_level_json(raw or "") or []
    if not isinstance(parsed, list):
        repaired = _model_repair_to_json_array(raw)
        if isinstance(repaired, list):
            parsed = repaired
        else:
            local_fails.append("JSON parse failed")
            parsed = []
    parsed = [_normalize_keyframe(x) for x in parsed if isinstance(x, dict) and x.get("shot_id")]
    return parsed, raw, local_fails, used

# ---------- Round2（策略版） ----------
def generate_keyframe_prompts_batched(
    pictures_json: List[Dict[str, Any]],
    *,
    characters: str,
    scenes: str,
    batch_size: int = 15,
    max_output_tokens: int = 30000,
    temperature: float = 0.4,
    continue_segments: int = 6,
    max_missing_retry_rounds: int = 3,
    parallel_workers: int = 4,  # 并行策略从 4 开始，失败降到 2；再失败切换到串行流式；再失败串行非流式
) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    order_map = _shot_order_map(pictures_json)
    all_shots = [s for s in pictures_json if s.get("shot_id")]
    shot_ids_input = [s["shot_id"] for s in all_shots]
    batches = _chunk_list(pictures_json, max(1, int(batch_size)))

    all_keyframes: List[Dict[str, Any]] = []
    all_raw_chunks: List[str] = []
    failures: List[str] = []
    used_model_holder = {"value": None}  # 避免 nonlocal 语法问题

    def _sort_all():  # 排序稳定
        all_keyframes.sort(key=lambda k: _sort_key_for(order_map, k))

    def _run_batches(mode: str, batch_indices: List[int], workers: int = 1) -> List[int]:
        """
        运行指定批次；返回解析失败的批次索引列表。
        mode: 'parallel_nonstream' | 'serial_stream' | 'serial_nonstream'
        """
        failed: List[int] = []

        if mode == "parallel_nonstream" and workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                fut2idx = {}
                for bi in batch_indices:
                    prompt = _render_round2_prompt(batches[bi], characters, scenes)
                    fut = ex.submit(
                        _run_one_batch, prompt,
                        temperature=temperature, max_output_tokens=max_output_tokens,
                        continue_segments=continue_segments, prefer_stream=False, on_stream=None
                    )
                    fut2idx[fut] = bi
                for fut in as_completed(fut2idx):
                    bi = fut2idx[fut]
                    parsed, raw, fs, um = fut.result()
                    if um and not used_model_holder["value"]:
                        used_model_holder["value"] = um
                    all_keyframes.extend(parsed); all_raw_chunks.append(raw)
                    if fs: failures.extend([f"batch#{bi+1}: {x}" for x in fs])
                    if not parsed: failed.append(bi)
            _sort_all()
            return failed

        # 串行（可流式/非流式）
        for bi in batch_indices:
            prompt = _render_round2_prompt(batches[bi], characters, scenes)
            parsed, raw, fs, um = _run_one_batch(
                prompt,
                temperature=temperature, max_output_tokens=max_output_tokens,
                continue_segments=continue_segments,
                prefer_stream=(mode == "serial_stream"),
                on_stream=(lambda chunk: None) if mode == "serial_stream" else None
            )
            if um and not used_model_holder["value"]:
                used_model_holder["value"] = um
            all_keyframes.extend(parsed); all_raw_chunks.append(raw)
            if fs: failures.extend([f"batch#{bi+1}: {x}" for x in fs])
            if not parsed: failed.append(bi)
        _sort_all()
        return failed

    # —— 策略链 —— #
    # S1: 并行非流式，workers=4（或传入值）
    start_workers = max(1, int(parallel_workers or 4))
    pending = list(range(len(batches)))
    failed_idx = _run_batches("parallel_nonstream", pending, workers=start_workers)

    # S2: 若失败，workers=2 重试失败项
    if failed_idx:
        failed_idx = _run_batches("parallel_nonstream", failed_idx, workers=2)

    # S3: 若仍失败，串行“流式”
    if failed_idx:
        failed_idx = _run_batches("serial_stream", failed_idx, workers=1)

    # S4: 若仍失败，串行“非流式”
    if failed_idx:
        failed_idx = _run_batches("serial_nonstream", failed_idx, workers=1)

    # 至此仍失败的批次，记录为彻底失败（降级在 _client 内部已自动进行）
    if failed_idx:
        failures.append(f"hard_failed_batches={ [i+1 for i in failed_idx] }")

    # —— 覆盖校验与缺失重生（3 轮） —— #
    def covered() -> set[str]:
        return {str(k.get("shot_id")) for k in all_keyframes if k.get("shot_id")}

    missing = [sid for sid in shot_ids_input if sid not in covered()]
    missing_detail: Dict[str, str] = {}

    retry_round = 0
    while missing and retry_round < max_missing_retry_rounds:
        retry_round += 1
        miss_batch = [s for s in all_shots if s["shot_id"] in missing]
        prompt = _render_round2_prompt(miss_batch, characters, scenes)
        parsed, raw, fs, _ = _run_one_batch(
            prompt,
            temperature=temperature, max_output_tokens=max_output_tokens,
            continue_segments=continue_segments,
            prefer_stream=False, on_stream=None
        )
        all_keyframes.extend(parsed); all_raw_chunks.append(raw or "")
        if fs:
            failures.extend([f"retry#{retry_round}: {x}" for x in fs])
        _sort_all()
        # 更新 missing
        missing = [sid for sid in shot_ids_input if sid not in covered()]

    # 仍缺，标注原因并占位补齐
    if missing:
        for sid in missing:
            missing_detail[sid] = "not returned by model after 3 retry rounds"
            shot = next((s for s in all_shots if s["shot_id"] == sid), {"shot_id": sid})
            all_keyframes.append(_placeholder_from_shot(shot))
        _sort_all()
        failures.append(f"filled_placeholders_for_missing_shots: {missing}")

    merged_raw = "\n\n---\n\n".join(all_raw_chunks)
    meta = {
        "used_model": used_model_holder["value"] or "",
        "failures": failures,
        "batches": len(batches),
        "retry_rounds": retry_round,
        "frames": len(all_keyframes),
        "shots_input": len(shot_ids_input),
        "shots_covered": len({k['shot_id'] for k in all_keyframes}),
        "missing_after_retries": missing,
        "missing_reasons": missing_detail,
    }
    return all_keyframes, merged_raw, meta

# ---------- 汇总打包（两轮） ----------
def generate_storyboard_package(
    *,
    story: str,
    characters: str,
    scenes: str = "",
    style: str = "cinematic, realistic",
    min_shots: int = 12,
    max_shots: int = 500,
    persist: bool = True,
    round1_max_tokens: int = 50000,
    round2_max_tokens: int = 30000,
    round2_batch_size: int = 15,
    round2_parallel_workers: int = 4,
) -> Dict[str, Any]:
    pics_json, pics_raw, meta1 = generate_pictures(
        story, style=style, min_shots=min_shots, max_shots=max_shots,
        max_output_tokens=round1_max_tokens
    )
    kf_json, kf_raw, meta2 = generate_keyframe_prompts_batched(
        pics_json or [], characters=characters, scenes=scenes,
        batch_size=round2_batch_size,
        max_output_tokens=round2_max_tokens,
        parallel_workers=round2_parallel_workers,
    )
    stem = new_run_id()
    pack = {
        "id": stem,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "inputs": {"style": style, "min_shots": min_shots, "max_shots": max_shots},
        "round1": {"used_model": meta1.get("used_model",""), "failures": meta1.get("failures",[]), "text_raw": pics_raw, "json": pics_json},
        "round2": {"used_model": meta2.get("used_model",""), "failures": meta2.get("failures",[]), "text_raw": kf_raw,  "json": kf_json,
                   "missing_after_retries": meta2.get("missing_after_retries", []),
                   "missing_reasons": meta2.get("missing_reasons", {})},
    }
    if persist:
        _, pics_url = persist_named_json(stem, "round1_pictures", pics_json or [])
        _, kf_url   = persist_named_json(stem, "round2_keyframes", kf_json or [])
        _, raw1_url = persist_named_text(stem, "round1_raw", pics_raw or "")
        _, raw2_url = persist_named_text(stem, "round2_raw", kf_raw or "")
        _, pkg_url  = persist_named_json(stem, "package", pack)
        pack["downloads"] = {
            "pictures_url": pics_url, "keyframes_url": kf_url,
            "round1_raw_url": raw1_url, "round2_raw_url": raw2_url, "package_url": pkg_url
        }
    return pack
