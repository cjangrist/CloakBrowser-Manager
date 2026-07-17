"""Shared test fixtures for backend tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Mock cloakbrowser BEFORE any backend module is imported.
# browser_manager.py does `from cloakbrowser import launch_persistent_context_async`
# at module level, and main.py imports BrowserManager which triggers it.
# main.py:381 also does `from cloakbrowser.config import CHROMIUM_VERSION`.
# ---------------------------------------------------------------------------

_mock_cloakbrowser = types.ModuleType("cloakbrowser")
_mock_cloakbrowser.launch_persistent_context_async = AsyncMock()  # type: ignore[attr-defined]

_mock_config = types.ModuleType("cloakbrowser.config")
_mock_config.CHROMIUM_VERSION = "0.0.0-test"  # type: ignore[attr-defined]

sys.modules.setdefault("cloakbrowser", _mock_cloakbrowser)
sys.modules.setdefault("cloakbrowser.config", _mock_config)


from backend import database as db  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_manager_runtime(monkeypatch: pytest.MonkeyPatch):
    """Keep startup preparation deterministic and offline in unit tests."""
    from backend import main

    runtime = {
        "wrapper_version": "0.0.0-test",
        "binary_version": "0.0.0-test",
        "binary_tier": "test",
        "platform": "test-platform",
    }
    monkeypatch.setattr(main, "prepare_cloakbrowser_runtime", lambda required: runtime)
    monkeypatch.setattr(main, "sync_extensions", lambda ids, root, version: [])
    monkeypatch.setattr(main.browser_mgr, "max_running_profiles", 0)
    main.browser_mgr.configure_extensions([])
    main.browser_mgr.running.clear()
    main.browser_mgr._launching.clear()


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point database module at a temp directory and init schema."""
    db_file = tmp_path / "profiles.db"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    return tmp_path


@pytest.fixture()
def sample_profile(tmp_db: Path):
    """Create and return a sample profile dict."""
    return db.create_profile(name="Test Profile", fingerprint_seed=12345)


@pytest.fixture()
def app_client(tmp_db: Path, monkeypatch: pytest.MonkeyPatch):
    """FastAPI TestClient with mocked DB and browser manager."""
    from backend import main

    # Patch lifespan-called methods to avoid subprocess calls (pkill, Xvnc)
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    from starlette.testclient import TestClient

    with TestClient(main.app) as client:
        yield client
