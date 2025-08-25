import json
from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from providers.llm.gemini import GeminiClient
from services.api.app.core.security import verify_api_key
from services.api.app.core.sse import sse_iter

router = APIRouter()
_client = None


def get_client():
    global _client
    if _client is None:
        _client = GeminiClient(
            model_candidates=[
                "models/gemini-2.5-pro",
                "models/gemini-2.5-flash",
                "models/gemini-1.5-pro-latest",
            ],
            on_max_tokens="continue",
            max_continue_segments=3,
        )
    return _client


class StreamReq(BaseModel):
    prompt: Any
    config: Dict[str, Any] = {}


@router.post("/stream", dependencies=[Depends(verify_api_key)])
def stream(req: StreamReq):
    gen, used, fails = get_client().stream_with_fallback(req.prompt, **(req.config or {}))
    headers = {
        "X-Used-Model": used or "<none>",
        "X-Fallback-Failures": json.dumps(fails, ensure_ascii=False),
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return StreamingResponse(sse_iter(gen), media_type="text/event-stream", headers=headers)
