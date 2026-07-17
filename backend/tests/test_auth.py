"""Tests for optional authentication middleware and endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


PROFILE_UUID = "123e4567-e89b-42d3-a456-426614174000"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def client_no_auth(tmp_db, monkeypatch):
    """TestClient with AUTH_TOKEN = None (auth disabled)."""
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", None)
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    with TestClient(main.app) as client:
        yield client


@pytest.fixture()
def client_auth(tmp_db, monkeypatch):
    """TestClient with AUTH_TOKEN = 'test-secret' (auth enabled)."""
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    with TestClient(main.app) as client:
        yield client


# ── Group A: AUTH_TOKEN not set ──────────────────────────────────────────────


def test_no_auth_profiles_accessible(client_no_auth: TestClient):
    resp = client_no_auth.get("/api/profiles")
    assert resp.status_code == 200


def test_no_auth_status_shows_not_required(client_no_auth: TestClient):
    resp = client_no_auth.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_required"] is False
    assert data["authenticated"] is False


def test_no_auth_login_noop(client_no_auth: TestClient):
    resp = client_no_auth.post("/api/auth/login", json={"token": "anything"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ── Group B: AUTH_TOKEN set ──────────────────────────────────────────────────


def test_auth_no_token_401(client_auth: TestClient):
    resp = client_auth.get("/api/profiles")
    assert resp.status_code == 401


def test_auth_wrong_bearer_401(client_auth: TestClient):
    resp = client_auth.get(
        "/api/profiles", headers={"Authorization": "Bearer wrong-token"}
    )
    assert resp.status_code == 401


def test_auth_correct_bearer_200(client_auth: TestClient):
    resp = client_auth.get(
        "/api/profiles", headers={"Authorization": "Bearer test-secret"}
    )
    assert resp.status_code == 200


def test_auth_custom_header_for_cdp_clients(client_auth: TestClient):
    resp = client_auth.get(
        "/api/profiles", headers={"X-Cloak-Auth-Token": "test-secret"}
    )
    assert resp.status_code == 200


def test_auth_correct_cookie_200(client_auth: TestClient):
    client_auth.cookies.set("auth_token", "test-secret")
    resp = client_auth.get("/api/profiles")
    assert resp.status_code == 200


def test_auth_status_unauthenticated(client_auth: TestClient):
    resp = client_auth.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_required"] is True
    assert data["authenticated"] is False


def test_auth_status_authenticated(client_auth: TestClient):
    client_auth.cookies.set("auth_token", "test-secret")
    resp = client_auth.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_required"] is True
    assert data["authenticated"] is True


def test_login_correct_sets_cookie(client_auth: TestClient):
    resp = client_auth.post("/api/auth/login", json={"token": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "auth_token" in resp.cookies


def test_login_wrong_token_401(client_auth: TestClient):
    resp = client_auth.post("/api/auth/login", json={"token": "wrong"})
    assert resp.status_code == 401


def test_logout_clears_cookie(client_auth: TestClient):
    # Login first
    client_auth.post("/api/auth/login", json={"token": "test-secret"})
    # Logout
    resp = client_auth.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_healthcheck_always_accessible(client_auth: TestClient):
    """GET /api/status must work without auth (Docker healthcheck)."""
    resp = client_auth.get("/api/status")
    assert resp.status_code == 200


def test_auth_status_always_accessible(client_auth: TestClient):
    """GET /api/auth/status must work without auth (frontend bootstrap)."""
    resp = client_auth.get("/api/auth/status")
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        f"/api/profiles/{PROFILE_UUID}/cdp",
        f"/api/profiles/{PROFILE_UUID}/cdp/",
        f"/api/profiles/{PROFILE_UUID}/cdp/json/version",
        f"/api/profiles/{PROFILE_UUID}/cdp/json/list/",
        f"/api/profiles/{PROFILE_UUID}/cdp/devtools/page/target-id",
    ],
)
def test_canonical_profile_cdp_paths_bypass_auth(path: str):
    from backend import main

    assert main._is_public_cdp_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/profiles/not-a-uuid/cdp",
        "/api/profiles/123e4567-e89b-12d3-a456-426614174000/cdp",
        f"/api/profiles/{PROFILE_UUID.upper()}/cdp",
        f"/api/profiles/{PROFILE_UUID}/cdpx",
        f"/api/profiles/{PROFILE_UUID}/cdp/admin",
        f"/api/profiles/{PROFILE_UUID}/cdp/devtools/page",
        f"/api/profiles/{PROFILE_UUID}/cdp/devtools/worker/target-id",
        f"/api/profiles/{PROFILE_UUID}/cdp/devtools/page/target-id/extra",
        f"/api/profiles/{PROFILE_UUID}/vnc",
        f"/api/profiles/{PROFILE_UUID}",
    ],
)
def test_noncanonical_or_non_cdp_paths_do_not_bypass_auth(path: str):
    from backend import main

    assert main._is_public_cdp_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        f"/api/profiles/{PROFILE_UUID}/cdp",
        f"/api/profiles/{PROFILE_UUID}/cdp/json/version",
        f"/api/profiles/{PROFILE_UUID}/cdp/json/list",
    ],
)
def test_cdp_http_discovery_reaches_route_without_auth(
    client_auth: TestClient,
    path: str,
):
    response = client_auth.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not running"


@pytest.mark.parametrize(
    "path",
    [
        f"/api/profiles/{PROFILE_UUID}",
        f"/api/profiles/{PROFILE_UUID}/status",
        f"/api/profiles/{PROFILE_UUID}/vnc",
        "/api/profiles/not-a-uuid/cdp",
    ],
)
def test_nearby_profile_routes_still_require_auth(
    client_auth: TestClient,
    path: str,
):
    assert client_auth.get(path).status_code == 401


def test_cdp_websocket_reaches_route_without_auth(client_auth: TestClient):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_auth.websocket_connect(f"/api/profiles/{PROFILE_UUID}/cdp"):
            pass
    assert exc_info.value.code == 4004


def test_vnc_websocket_still_requires_auth(client_auth: TestClient):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_auth.websocket_connect(f"/api/profiles/{PROFILE_UUID}/vnc"):
            pass
    assert exc_info.value.code == 4401
