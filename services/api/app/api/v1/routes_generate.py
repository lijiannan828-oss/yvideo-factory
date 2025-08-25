from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from providers.llm.gemini import GeminiClient
from services.api.app.core.security import verify_api_key

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


class GenerateReq(BaseModel):
    prompt: Any
    config: Dict[str, Any] = {}


@router.post("/generate", dependencies=[Depends(verify_api_key)])
def generate(req: GenerateReq):
    try:
        text, used, fails = get_client().generate_with_fallback(req.prompt, **(req.config or {}))
        if not (text and text.strip()):
            raise HTTPException(500, detail={"error": "empty_output", "failures": fails})
        return {"used_model": used, "failures": fails, "output": text}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
