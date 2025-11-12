from pathlib import Path
import sys

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from app import app  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def test_theme_nextdebug_returns_raw_payload(monkeypatch, client):
    async def fake_fetch_theme_resources(name: str, identity: str):
        return {
            "tag_slug": "prowess",
            "color_slug": "jeskai",
            "label": "Jeskai",
            "tag_html_url": "https://edhrec.com/tags/prowess/jeskai",
            "json_url": "https://edhrec.com/_next/data/mock/tags/prowess/jeskai.json",
            "header": "Jeskai Prowess | EDHREC",
            "description": "Sample description",
            "data": {"pageProps": {"collections": []}},
        }

    monkeypatch.setattr("app._fetch_theme_resources", fake_fetch_theme_resources)

    resp = client.get("/edhrec/theme_nextdebug", params={"name": "prowess", "identity": "wur"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["data"] == {"pageProps": {"collections": []}}
    assert payload["tag"] == "prowess"
    assert payload["identity"] == "jeskai"
    assert payload["json_url"].endswith("prowess/jeskai.json")


def test_theme_route_returns_404_when_upstream_missing(monkeypatch, client):
    class DummyClient:
        async def get(self, url: str, follow_redirects: bool = True):
            request = httpx.Request("GET", url)
            return httpx.Response(404, request=request)

    monkeypatch.setattr(app.state, "client", DummyClient())

    resp = client.get("/edhrec/theme", params={"name": "prowess", "identity": "wur"})
    assert resp.status_code == 404
    detail = resp.json().get("detail", "")
    assert "Resource not found" in detail


def test_theme_route_returns_502_when_upstream_errors(monkeypatch, client):
    class DummyClient:
        async def get(self, url: str, follow_redirects: bool = True):
            request = httpx.Request("GET", url)
            return httpx.Response(500, request=request)

    monkeypatch.setattr(app.state, "client", DummyClient())

    resp = client.get("/edhrec/theme", params={"name": "prowess", "identity": "wur"})
    assert resp.status_code == 502
    detail = resp.json().get("detail", "")
    assert "Upstream fetch failed" in detail


@pytest.mark.parametrize("status_code", [404, 502])
def test_theme_nextdebug_propagates_http_errors(monkeypatch, client, status_code):
    from fastapi import HTTPException

    async def fake_fetch_theme_resources(name: str, identity: str):
        raise HTTPException(status_code=status_code, detail="boom")

    monkeypatch.setattr("app._fetch_theme_resources", fake_fetch_theme_resources)

    resp = client.get("/edhrec/theme_nextdebug", params={"name": "prowess", "identity": "wur"})
    assert resp.status_code == status_code
    assert resp.json()["detail"] == "boom"

