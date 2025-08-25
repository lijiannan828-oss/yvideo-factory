# -*- coding: utf-8 -*-
"""
===========================================================
Gemini Gateway - 基础交互封装（Pro 优先，失败降级）
===========================================================

功能:
    - 封装 Google Gemini 模型调用逻辑，支持多种模式：
        1) 单轮文本生成 (generate_with_fallback)
        2) 多轮对话 (chat_with_fallback)
        3) 流式输出 (stream_with_fallback)
        4) JSON 结构化输出 (generate_json)
    - 内置模型优先级与降级机制（强制 Pro 优先）:
        gemini-2.5-pro → gemini-2.5-flash → gemini-1.5-pro-latest
    - 遇到 MAX_TOKENS 截断时，可按策略自动追加“续写段”

依赖:
    pip install -U google-genai python-dotenv

环境变量(.env):
    GOOGLE_API_KEY   : Google Gemini API key

===========================================================
"""
import os
import time
import json
import random
from typing import Optional, List, Dict, Any, Tuple, Iterable
from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
    from google.genai.types import FinishReason
except Exception as e:
    raise SystemExit(
        "导入 google.genai 失败：%s\n"
        "排查：\n"
        "1) pip install -U google-genai python-dotenv\n"
        "2) 项目中是否存在本地目录/包名为 `google/`？会遮挡官方包，请改名\n"
        "3) 如安装过 `google` 元包：pip uninstall -y google\n" % e
    )

load_dotenv()

# ---------------- 路由（全部强制 Pro 优先） ----------------
ROUTES = {
    "short": [
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash",
        "models/gemini-1.5-pro-latest",
    ],
    "stream": [
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash",
        "models/gemini-1.5-pro-latest",
    ],
    "json": [
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash",
        "models/gemini-1.5-pro-latest",
    ],
    "longform": [
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash",
        "models/gemini-1.5-pro-latest",
    ],
}

FINISH_REASON_STOP = {"STOP"}  # 用“裸名字”集合判断
# 更稳健的瞬时错误关键词
TRANSIENT_HINTS = (
    "code: 429", "status: RESOURCE_EXHAUSTED", "rate limit",
    "code: 500", "code: 502", "code: 503", "code: 504",
    "status: INTERNAL", "status: UNAVAILABLE", "status: DEADLINE_EXCEEDED",
    "temporarily unavailable", "connection reset", "timeout",
    "rst_stream", "goaway"
)


def _normalize_model_name(name: str) -> str:
    return name if name.startswith("models/") else f"models/{name}"


def _jittered_backoff_sleep(attempt: int, base=0.8, factor=2.0, cap=30.0):
    """指数退避 + 抖动"""
    time.sleep(min(cap, base * (factor ** attempt) * (0.8 + random.random() * 0.4)))


def _is_transient_error(exc: Exception) -> bool:
    low = str(exc).lower()
    return any(h in low for h in TRANSIENT_HINTS)


def _finish_name(fr) -> str:
    """
    统一把 finish_reason 规范化为枚举名的裸字符串：
    FinishReason.STOP -> "STOP"
    "FinishReason.MAX_TOKENS" -> "MAX_TOKENS"
    "STOP" -> "STOP"
    None / 未知 -> "UNKNOWN"
    """
    if fr is None:
        return "UNKNOWN"
    if isinstance(fr, FinishReason):
        return fr.name or "UNKNOWN"
    s = str(fr)
    return s.split(".")[-1] if "." in s else s


# ---- JSON Schema 构建器（支持 description / minItems / maxItems）----
TYPE_MAP = {
    "string": types.Type.STRING,
    "number": types.Type.NUMBER,
    "integer": types.Type.INTEGER,
    "boolean": types.Type.BOOLEAN,
    "array": types.Type.ARRAY,
    "object": types.Type.OBJECT,
}


def build_schema_from_dict(schema_dict: Dict[str, Any]) -> types.Schema:
    if not isinstance(schema_dict, dict):
        raise ValueError("schema must be a dict")
    t = schema_dict.get("type")
    t_enum = t if isinstance(t, types.Type) else TYPE_MAP.get(str(t).lower())
    if t_enum is None:
        raise ValueError(f"Unsupported schema type: {t}")

    kwargs: Dict[str, Any] = {"type": t_enum}

    # 通用可选字段
    if "description" in schema_dict:
        kwargs["description"] = schema_dict["description"]

    if t_enum == types.Type.OBJECT:
        props = schema_dict.get("properties") or {}
        kwargs["properties"] = {k: build_schema_from_dict(v) for k, v in props.items()}
        if "required" in schema_dict:
            kwargs["required"] = list(schema_dict["required"])

    if t_enum == types.Type.ARRAY:
        items = schema_dict.get("items")
        if items:
            kwargs["items"] = build_schema_from_dict(items)
        if "minItems" in schema_dict:
            kwargs["min_items"] = int(schema_dict["minItems"])
        if "maxItems" in schema_dict:
            kwargs["max_items"] = int(schema_dict["maxItems"])

    return types.Schema(**kwargs)


class GeminiClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_candidates: Optional[List[str]] = None,
        default_generation_config: Optional[Dict[str, Any]] = None,
        on_max_tokens: str = "continue",  # "continue" | "return" | "raise"
        continue_prompt: str = "继续上文，从中断处续写；不要重复已输出的句子；保持相同风格与语言。",
        max_continue_segments: int = 4,
    ):
        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("请设置 GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)

        # 默认用 longform 路由（Pro 优先）
        self.model_candidates = [
            _normalize_model_name(m) for m in (model_candidates or ROUTES["longform"])
        ]

        self.default_cfg = default_generation_config or {
            "temperature": 0.7,
            "top_p": 0.95,
            "max_output_tokens": 512,
        }
        self.on_max_tokens = on_max_tokens
        self.continue_prompt = continue_prompt
        self.max_continue_segments = max_continue_segments

    # ---------- 外部切换路由 ----------
    def use_route(self, route_name: str):
        if route_name not in ROUTES:
            raise ValueError(f"未知路由: {route_name}")
        self.model_candidates = [_normalize_model_name(m) for m in ROUTES[route_name]]

    # ---------- 内部公共 ----------
    @staticmethod
    def _extract_text_and_reason(resp) -> Tuple[str, str]:
        """
        更稳健的提取逻辑：
        1) 先拿 resp.text
        2) 空的话，尝试从 candidates[0].content.parts 里把所有 text 拼接
        3) 兜底返回 "" 与 finish_reason
        """
        txt = getattr(resp, "text", "") or ""
        reason = "UNKNOWN"

        cands = getattr(resp, "candidates", None)
        if cands:
            reason = _finish_name(getattr(cands[0], "finish_reason", None))

            # 如果 resp.text 为空，尝试从 parts 重建
            if not txt.strip():
                try:
                    content = getattr(cands[0], "content", None)
                    parts = getattr(content, "parts", None) if content else None
                    if parts:
                        buf = []
                        for p in parts:
                            # google-genai 的 part 可能是对象也可能是 dict
                            t = getattr(p, "text", None)
                            if t is None and isinstance(p, dict):
                                t = p.get("text")
                            if t:
                                buf.append(t)
                        if buf:
                            txt = "".join(buf)
                except Exception:
                    # 安全兜底，保持 txt 为空以触发后续补救逻辑
                    pass

        return txt, reason

    def _call_with_retry(self, fn, retries=4):
        last = None
        for i in range(retries + 1):
            try:
                return fn()
            except Exception as e:
                last = e
                if i < retries and _is_transient_error(e):
                    _jittered_backoff_sleep(i)
                    continue
                break
        raise last

    # ---------- 单轮：降级 + 续写 + empty_text 补救 ----------
    def generate_with_fallback(self, prompt: Any, **kwargs) -> Tuple[str, str, List[str]]:
        failures: List[str] = []
        cfg_dict = dict(self.default_cfg)
        for k in (
                "temperature", "max_output_tokens", "top_p", "top_k",
                "candidate_count", "stop_sequences",
                "response_mime_type", "response_schema",  # ← 新增
        ):
            if k in kwargs:
                cfg_dict[k] = kwargs.pop(k)
        cfg = types.GenerateContentConfig(**cfg_dict)

        # —— 内部参数（不要传给 google 接口）
        on_max = kwargs.pop("on_max_tokens", self.on_max_tokens)
        seg_limit = int(kwargs.pop("continue_segments", self.max_continue_segments))
        ctx_chars = int(kwargs.pop("continue_context_chars", 3000))  # 新增：续写时携带多少“上文”

        def _invoke(name: str, content: Any):
            return self.client.models.generate_content(
                model=name, contents=content, config=cfg, **kwargs
            )

        for name in self.model_candidates:
            try:
                resp = self._call_with_retry(lambda: _invoke(name, prompt))
                txt, reason = self._extract_text_and_reason(resp)

                # 正常完成且有文本
                if reason in FINISH_REASON_STOP and txt.strip():
                    return txt, name, failures

                # 被 MAX_TOKENS 截断：按策略处理
                if reason in {"MAX_TOKENS", "UNKNOWN"}:
                    if not txt.strip():
                        failures.append(f"{name}: finish_reason={reason}, empty_first_chunk")
                        continue
                    if on_max == "return":
                        return txt, name, failures
                    if on_max == "raise":
                        failures.append(f"{name}: finish_reason={reason}, partial_len={len(txt)}")
                        continue

                    # 续写
                    total = [txt]
                    seg = 0
                    while seg < seg_limit:
                        seg_prompt = (
                            f"{self.continue_prompt}\n\n"
                             f"--- 上文 ---\n{''.join(total)[-ctx_chars:]}\n--- 续写 ---"
                        )
                        seg_resp = self._call_with_retry(lambda: _invoke(name, seg_prompt))
                        seg_txt, seg_reason = self._extract_text_and_reason(seg_resp)
                        if seg_txt:
                            total.append(seg_txt)
                        if seg_reason in FINISH_REASON_STOP:
                            merged = "".join(total)
                            if merged.strip():
                                return merged, name, failures
                            break
                        if seg_reason not in ("MAX_TOKENS", "UNKNOWN"):
                            break
                        seg += 1
                    if "".join(total).strip():
                        return "".join(total), name, failures
                    failures.append(f"{name}: finish_reason={reason}, parts_empty_after_continue")
                    continue

                # 其它 finish_reason 但文本为空：补救一次（强制 JSON 体裁）
                if not txt.strip():
                    failures.append(f"{name}: finish_reason={reason or 'UNKNOWN'}, empty_text")
                    try:
                        patched_prompt = f"{prompt}\n\n只输出合法 JSON 数组；不要 Markdown、不要解释、不要空行。"
                        cfg_json = types.GenerateContentConfig(**{**cfg_dict, "response_mime_type": "application/json"})
                        patched = self._call_with_retry(
                            lambda: self.client.models.generate_content(model=name, contents=patched_prompt,
                                                                        config=cfg_json, **kwargs)
                        )
                        ptxt, preason = self._extract_text_and_reason(patched)
                        if ptxt.strip():
                            return ptxt, name, failures
                        else:
                            failures.append(f"{name}: patch_empty_text (finish_reason={preason})")
                    except Exception as e2:
                        failures.append(f"{name}: patch_retry_exception {e2}")
                    continue

                # 有文本就返回
                return txt, name, failures

            except Exception as e:
                failures.append(f"{name}: EXCEPTION {e}")

        return "", "", failures

    # ---------- 流式：统一入口 + 一次重连 + 非流式暖场 + 降级 ----------
    def stream_with_fallback(self, prompt: Any, **kwargs) -> Tuple[Iterable[str], str, List[str]]:
        """
        流式：先试当前候选（Pro 优先），若首个流事件拿不到则短重连一次；
        仍失败则走“非流式暖场”再降级到下一模型。
        """
        failures: List[str] = []

        # —— 关键点：防止外部把 stream=True 传进来，造成 generate_content 接口报错
        kwargs.pop("stream", None)
        # —— 同理把内部扩展参数弹掉，避免传给 API
        kwargs.pop("continue_context_chars", None)
        kwargs.pop("continue_segments", None)
        kwargs.pop("on_max_tokens", None)
        # 组装生成配置
        cfg_dict = dict(self.default_cfg)
        for k in (
                "temperature", "max_output_tokens", "top_p", "top_k",
                "candidate_count", "stop_sequences",
                "response_mime_type", "response_schema",  # ← 新增
        ):
            if k in kwargs:
                cfg_dict[k] = kwargs.pop(k)
        cfg = types.GenerateContentConfig(**cfg_dict)

        def try_stream_once(name: str):
            """仅尝试一次流，逐个 yield chunk；返回是否有产出。"""
            yielded = False
            # ✅ 使用专门的流式接口
            stream = self.client.models.generate_content_stream(
                model=name, contents=prompt, config=cfg, **kwargs
            )
            for ev in stream:
                # 事件里可能直接有 text，也可能需要从 parts 聚合
                chunk = getattr(ev, "text", "") or ""
                if chunk:
                    yielded = True
                    yield chunk
                    continue
                cands = getattr(ev, "candidates", None)
                if cands and getattr(cands[0], "content", None):
                    parts = getattr(cands[0].content, "parts", None)
                    if parts:
                        buf = []
                        for p in parts:
                            t = getattr(p, "text", "") or (p.get("text") if isinstance(p, dict) else "")
                            if t:
                                buf.append(t)
                        if buf:
                            yielded = True
                            yield "".join(buf)
            return yielded

        def try_stream(name: str):
            """首连拿不到事件 → 短重连一次；都拿不到就抛 stream_no_events。"""
            gen1 = try_stream_once(name)
            first = None
            for first in gen1:
                break
            if first is None:
                # 短退避后重连一次
                time.sleep(0.15)
                gen2 = try_stream_once(name)
                for first in gen2:
                    break
                if first is None:
                    raise RuntimeError("stream_no_events")

                def re2():
                    yield first
                    for rest in gen2:
                        yield rest

                return re2()

            def re1():
                yield first
                for rest in gen1:
                    yield rest

            return re1()

        def non_stream_once(name: str) -> str:
            """非流式暖场：若拿不到文本就抛 single_shot_empty。"""
            resp = self.client.models.generate_content(model=name, contents=prompt, config=cfg, **kwargs)
            txt = getattr(resp, "text", "") or ""
            if not txt.strip():
                raise RuntimeError("single_shot_empty")
            return txt

        # —— 降级主循环（Pro → Flash → 1.5）
        for name in self.model_candidates:
            try:
                gen = try_stream(name)
                # peek 第一段，保证确实有流
                first = None
                for first in gen:
                    break
                if first is None:
                    raise RuntimeError("stream_no_events")

                def rechain():
                    yield first
                    for rest in gen:
                        yield rest

                return rechain(), name, failures
            except Exception as e:
                failures.append(f"{name}: STREAM_FAIL {e}")
                # 非流式暖场再试（避免空播）
                try:
                    txt = non_stream_once(name)

                    def single_iter():
                        yield txt

                    return single_iter(), name, failures
                except Exception as e2:
                    failures.append(f"{name}: NON_STREAM_FAIL {e2}")
                    continue

        # 所有模型都失败
        return iter(()), "", failures

    # ---------- JSON ----------
    def generate_json(self, prompt: Any, schema: Dict[str, Any], **kwargs) -> Tuple[Dict[str, Any], str, List[str]]:
        failures: List[str] = []
        cfg_dict = dict(self.default_cfg)
        cfg_dict.update({
            "response_mime_type": "application/json",
            "response_schema": build_schema_from_dict(schema),
        })
        for k in ("temperature", "max_output_tokens", "top_p", "top_k", "stop_sequences"):
            if k in kwargs:
                cfg_dict[k] = kwargs.pop(k)
        cfg = types.GenerateContentConfig(**cfg_dict)

        def _invoke(name: str):
            return self.client.models.generate_content(model=name, contents=prompt, config=cfg, **kwargs)

        for name in self.model_candidates:
            try:
                resp = self._call_with_retry(lambda: _invoke(name))
                txt, reason = self._extract_text_and_reason(resp)
                if txt.strip():
                    try:
                        return json.loads(txt), name, failures
                    except Exception as e:
                        failures.append(f"{name}: JSON_PARSE_FAIL {e}; raw={txt[:120]}...")
                        continue
                failures.append(f"{name}: JSON_EMPTY (finish_reason={reason})")
            except Exception as e:
                failures.append(f"{name}: EXCEPTION {e}")

        return {}, "", failures

    # ---------- Chat ----------
    def chat_with_fallback(self, messages: List[Dict[str, Any]], **kwargs) -> Tuple[str, str, List[str]]:
        """
        messages 例子：
        [
          {"role": "user", "parts": ["你好，用一句话介绍机器学习"]},
          {"role": "model","parts": ["机器学习是..."]},
          {"role": "user", "parts": ["继续"]},
        ]
        """
        norm = []
        for m in messages:
            role = m.get("role", "user").lower()
            parts = m.get("parts", [])
            if isinstance(parts, str):
                parts = [parts]
            norm.append({
                "role": "model" if role in ("model", "assistant") else "user",
                "parts": [{"text": p if isinstance(p, str) else str(p.get("text",""))} for p in parts]
            })
        # 不再强制注入 stop_sequences，调用方可自行传入
        return self.generate_with_fallback(norm, **kwargs)


# ---------------- Smoke Tests ----------------
def _smoke_tests():
    client = GeminiClient(
        model_candidates=[
            "models/gemini-2.5-pro",
            "models/gemini-2.5-flash",
            "models/gemini-1.5-pro-latest",
        ],
        on_max_tokens="continue",
        max_continue_segments=3,
    )
    print("候选模型顺序：", client.model_candidates)

    # 1) 短输出
    client.use_route("short")
    txt, used, fails = client.generate_with_fallback(
        "Return exactly one word: ping",
        max_output_tokens=5000,
        on_max_tokens="return",
    )
    print("\n[Test#1] used_model =", used or "<none>")
    if fails: print("failures:", *fails, sep="\n  - ")
    print("output:", txt.strip() or "<EMPTY>")

    # 2) 中文短句
    client.use_route("short")
    txt2, used2, fails2 = client.generate_with_fallback(
        "用一句中文总结：大型语言模型的关键价值是什么？",
        max_output_tokens=5000,
        on_max_tokens="return",
    )
    print("\n[Test#2] used_model =", used2 or "<none>")
    if fails2: print("failures:", *fails2, sep="\n  - ")
    print("output:", txt2.strip() or "<EMPTY>")

    # 3) 流式
    client.use_route("stream")
    gen, used3, fails3 = client.stream_with_fallback(
        "请连续输出 3 句关于 AI 的短句，每句以句号结尾。",
        max_output_tokens=5000
    )
    print("\n[Test#3] used_model =", used3 or "<none>")
    if fails3: print("failures:", *fails3, sep="\n  - ")
    print("stream output: ", end="")
    for chunk in gen:
        print(chunk, end="")
    print()

    # 4) JSON
    client.use_route("json")
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "主题标题"},
            "bullets": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "string", "description": "要点"}
            }
        },
        "required": ["title", "bullets"]
    }
    j, used4, fails4 = client.generate_json(
        "以“AI 在内容生产中的应用”为题，返回 {title, bullets[3]} 的 JSON。",
        schema=schema,
        max_output_tokens=5000,
        temperature=0.2
    )
    print("\n[Test#4] used_model =", used4 or "<none>")
    if fails4: print("failures:", *fails4, sep="\n  - ")
    print("json output:", j or {})

    # 5) Chat
    client.use_route("longform")
    messages = [
        {"role": "user", "parts": ["用一句话解释什么是机器学习？"]},
        {"role": "model","parts": ["机器学习是让计算机从数据中学习模式以进行预测或决策的方法。"]},
        {"role": "user", "parts": ["给一个生活中的简单例子。"]},
    ]
    txt5, used5, fails5 = client.chat_with_fallback(
        messages, max_output_tokens=5000, temperature=0.3, on_max_tokens="return"
    )
    print("\n[Test#5] used_model =", used5 or "<none>")
    if fails5: print("failures:", *fails5, sep="\n  - ")
    print("output:", txt5.strip() or "<EMPTY>")


if __name__ == "__main__":
    _smoke_tests()


