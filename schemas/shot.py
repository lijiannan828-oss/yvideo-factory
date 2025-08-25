from pydantic import BaseModel
class ShotSpec(BaseModel):
    shot_id: str
    intent: str
    duration_s: float | None = None
