from pydantic import BaseModel
from typing import List
class AssemblyPlan(BaseModel):
    video_id: str
    shots: List[dict] = []
    voice: List[dict] = []
    sfx: List[dict] = []
    bgm: dict = {}
