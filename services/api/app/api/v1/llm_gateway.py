from fastapi import APIRouter

from .routes_chat import router as _chat
from .routes_generate import router as _gen
from .routes_json import router as _json
from .routes_stream import router as _stream

router = APIRouter()
router.include_router(_gen)
router.include_router(_chat)
router.include_router(_json)
router.include_router(_stream)
