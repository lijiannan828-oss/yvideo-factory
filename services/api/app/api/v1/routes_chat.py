from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any
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

class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' | 'model'")
    parts: List[Any]

class ChatReq(BaseModel):
    messages: List[ChatMessage]
    config: Dict[str, Any] = {}

@router.post("/chat", dependencies=[Depends(verify_api_key)])
def chat(req: ChatReq):
    messages = [m.model_dump() for m in req.messages]
    text, used, fails = get_client().chat_with_fallback(messages, **(req.config or {}))
    if not (text and text.strip()):
        raise HTTPException(500, detail={"error": "empty_output", "failures": fails})
    return {"used_model": used, "failures": fails, "output": text}
