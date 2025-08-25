import os

from fastapi import Header, HTTPException, status

SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "").strip()


def verify_api_key(x_api_key: str | None = Header(default=None)):
    if SERVICE_API_KEY and (not x_api_key or x_api_key != SERVICE_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
