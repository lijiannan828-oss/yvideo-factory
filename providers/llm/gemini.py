# providers/llm/gemini.py (patch6b) — full backward-compat layer + model_candidates property
import os, json, time
from typing import Iterable, Any, Dict, Optional, List, Tuple
from dotenv import load_dotenv
load_dotenv()

try:
    from google import genai
    from google.genai import types
except Exception as e:
    raise SystemExit("导入 google.genai 失败：%s\n请先 `pip install -U google-genai python-dotenv`。" % e)

# ---------- helpers ----------
def _to_text(prompt: Any) -> str:
    """安全归一：把任意结构转为纯文本，避免使用 Content/Part 的属性"""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, (int, float, bool)):
        return str(prompt)
    if isinstance(prompt, dict):
        try:
            return json.dumps(prompt, ensure_ascii=False, separators=(',', ':'))
        except Exception:
            return str(prompt)
    if isinstance(prompt, (list, tuple)):
        try:
            return "\n".join(_to_text(x) for x in prompt)
        except Exception:
            return "\n".join(map(str, prompt))
    return str(prompt)

def _as_response_schema(json_schema: Optional[Dict]) -> Optional[Any]:
    if not json_schema: return None
    try:
        if hasattr(types, "Schema") and hasattr(types.Schema, "from_json"):
            return types.Schema.from_json(json_schema)  # 兼容 0.2.x
    except Exception:
        pass
    return json_schema  # 兼容 0.3.x

def _sleep_backoff(i: int): time.sleep(min(1.5 * (2 ** i), 6.0))

# ---------- client ----------
class GeminiClient:
    """
    Backward compatibility:
      - __init__(models=..., **kwargs) 接受旧参数 model_candidates / default_generation_config /
        on_max_tokens / max_continue_segments / continue_prompt
      - 暴露 model_candidates 属性（与 models 同步）
      - 提供 generate_with_fallback / stream_with_fallback / chat_with_fallback / generate_json(返回三元组)
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        models: Optional[List[str]] = None,
        default_cfg: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        # 兼容旧名称
        if models is None:
            mc = kwargs.get("model_candidates")
            if isinstance(mc, (list, tuple)) and mc:
                models = list(mc)
        default_generation_config = kwargs.get("default_generation_config")
        if default_generation_config and not default_cfg:
            default_cfg = dict(default_generation_config)  # shallow copy

        # “超长输出续写”策略参数（先存起来，后续如需可支持真正续写）
        self.on_max_tokens = kwargs.get("on_max_tokens")  # e.g., "continue"
        self.max_continue_segments = kwargs.get("max_continue_segments", 0)
        self.continue_prompt = kwargs.get("continue_prompt") or ""

        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("请设置 GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self._models = models or [
            "models/gemini-2.5-pro",
            "models/gemini-2.5-flash",
            "models/gemini-1.5-pro-latest",
        ]
        self.default_cfg = {"temperature": 0.5, "top_p": 0.95, "max_output_tokens": 2048}
        if default_cfg:
            self.default_cfg.update(default_cfg)

    # property: models & model_candidates（互为别名）
    @property
    def models(self) -> List[str]:
        return self._models
    @models.setter
    def models(self, v: List[str]):
        self._models = list(v or [])
    @property
    def model_candidates(self) -> List[str]:
        return self._models
    @model_candidates.setter
    def model_candidates(self, v: List[str]):
        self._models = list(v or [])

    # -------- core single-call --------
    def _mk_cfg(self, overrides: Dict[str, Any], json_mode: bool=False, schema: Any=None):
        # 放宽 allow，允许 response_mime_type 直接透传
        allow = {"temperature","top_p","top_k","max_output_tokens","stop_sequences","candidate_count","response_mime_type"}
        merged = {k: v for k, v in (self.default_cfg | (overrides or {})).items() if k in allow}
        if json_mode:
            merged["response_mime_type"] = "application/json"
            if schema is not None:
                merged["response_schema"] = schema
        return types.GenerateContentConfig(**merged)

    def generate_text(self, prompt: Any, **cfg_overrides) -> str:
        text_prompt = _to_text(prompt)
        cfg = self._mk_cfg(cfg_overrides)
        resp = self.client.models.generate_content(model=self._models[0], contents=text_prompt, config=cfg)
        return getattr(resp, "text", "") or ""

    def stream_text(self, prompt: Any, **cfg_overrides) -> Iterable[str]:
        text_prompt = _to_text(prompt)
        cfg = self._mk_cfg(cfg_overrides)
        stream = self.client.models.generate_content_stream(model=self._models[0], contents=text_prompt, config=cfg)
        for ev in stream:
            chunk = getattr(ev, "text", "") or ""
            if chunk:
                yield chunk

    def generate_json_single(self, prompt: Any, json_schema: Dict, **cfg_overrides) -> Dict:
        text_prompt = _to_text(prompt)
        schema = _as_response_schema(json_schema)
        cfg = self._mk_cfg(cfg_overrides, json_mode=True, schema=schema)
        resp = self.client.models.generate_content(model=self._models[0], contents=text_prompt, config=cfg)
        raw = getattr(resp, "text", "") or ""
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except Exception:
            cleaned = raw.strip().strip('`').replace('\n', ' ').strip()
            return json.loads(cleaned)

    # -------- compatibility: *with_fallback --------
    def generate_with_fallback(self, prompt: Any, **cfg_overrides) -> Tuple[str, str, List[str]]:
        text_prompt = _to_text(prompt)
        failures: List[str] = []
        for i, model in enumerate(self._models):
            try:
                cfg = self._mk_cfg(cfg_overrides)
                resp = self.client.models.generate_content(model=model, contents=text_prompt, config=cfg)
                txt = getattr(resp, "text", "") or ""
                if txt.strip():
                    return txt, model, failures
                failures.append(f"{model}: EMPTY")
            except Exception as e:
                failures.append(f"{model}: EXCEPTION {e}")
                _sleep_backoff(i)
        return "", "", failures

    def stream_with_fallback(self, prompt: Any, **cfg_overrides) -> Tuple[Iterable[str], str, List[str]]:
        text_prompt = _to_text(prompt)
        failures: List[str] = []
        for i, model in enumerate(self._models):
            try:
                cfg = self._mk_cfg(cfg_overrides)
                stream = self.client.models.generate_content_stream(model=model, contents=text_prompt, config=cfg)
                def _gen():
                    for ev in stream:
                        chunk = getattr(ev, "text", "") or ""
                        if chunk:
                            yield chunk
                return _gen(), model, failures
            except Exception as e:
                failures.append(f"{model}: EXCEPTION {e}")
                _sleep_backoff(i)
        def _empty():
            if False:
                yield ""
        return _empty(), "", failures

    def chat_with_fallback(self, messages: List[Dict[str, Any]], **cfg_overrides) -> Tuple[str, str, List[str]]:
        parts = []
        for m in messages or []:
            role = m.get("role", "user")
            content = m.get("parts", [])
            if isinstance(content, (list, tuple)):
                content = " ".join(_to_text(p) for p in content)
            else:
                content = _to_text(content)
            parts.append(f"{role}: {content}")
        return self.generate_with_fallback("\n".join(parts), **cfg_overrides)

    def generate_json(self, prompt: Any, schema: Dict, **cfg_overrides) -> Tuple[Dict, str, List[str]]:
        failures: List[str] = []
        text_prompt = _to_text(prompt)
        for i, model in enumerate(self._models):
            try:
                sch = _as_response_schema(schema)
                cfg = self._mk_cfg(cfg_overrides, json_mode=True, schema=sch)
                resp = self.client.models.generate_content(model=model, contents=text_prompt, config=cfg)
                raw = getattr(resp, "text", "") or ""
                if not raw.strip():
                    failures.append(f"{model}: EMPTY")
                    continue
                try:
                    return json.loads(raw), model, failures
                except Exception:
                    cleaned = raw.strip().strip('`').replace('\n', ' ').strip()
                    return json.loads(cleaned), model, failures
            except Exception as e:
                failures.append(f"{model}: EXCEPTION {e}")
                _sleep_backoff(i)
        return {}, "", failures
