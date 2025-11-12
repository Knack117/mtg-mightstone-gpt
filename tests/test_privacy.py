import re


def test_privacy_fastapi():
    try:
        from app import app as fastapi_app  # type: ignore
        from fastapi.testclient import TestClient
    except Exception:
        return  # skip if not FastAPI
    client = TestClient(fastapi_app)
    resp = client.get("/privacy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "").lower()
    assert re.search(r"<h1>\s*Mightstone Privacy Policy\s*</h1>", resp.text)
