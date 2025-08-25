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


class JSONReq(BaseModel):
    prompt: str
    json_schema: Dict[str, Any]
    config: Dict[str, Any] = {}


@router.post("/generate_json", dependencies=[Depends(verify_api_key)])
def generate_json(req: JSONReq):
    obj, used, fails = get_client().generate_json(
        prompt=req.prompt, schema=req.json_schema, **(req.config or {})
    )
    if not obj:
        raise HTTPException(500, detail={"error": "json_empty", "failures": fails})
    return {"used_model": used, "failures": fails, "output": obj}
