from pydantic import BaseModel
class KeyframeSpec(BaseModel):
    shot_id: str
    frame_idx: int
    prompt: str
