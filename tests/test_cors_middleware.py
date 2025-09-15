# tests/test_cors_middleware.py
from fastapi.testclient import TestClient
from services.api.app.main import app

# 假设 config.py 里 CORS_ORIGINS = "http://localhost:3000,https://luxivideo.com"

def test_cors_allow_origin_localhost():
    client = TestClient(app)
    origin = "http://localhost:3000"
    response = client.get("/", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin

def test_cors_allow_origin_luxivideo():
    client = TestClient(app)
    origin = "https://luxivideo.com"
    response = client.get("/", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin

def test_cors_deny_unknown_origin():
    client = TestClient(app)
    origin = "https://not-allowed.com"
    response = client.get("/", headers={"Origin": origin})
    # FastAPI CORS middleware will not set allow-origin header for unknown origins
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
