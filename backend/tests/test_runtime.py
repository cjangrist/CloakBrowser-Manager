"""Tests for environment parsing and licensed runtime preparation."""

from __future__ import annotations

import importlib.metadata
import sys
import types
from types import SimpleNamespace

import pytest

from backend import runtime


def _install_runtime_modules(
    monkeypatch: pytest.MonkeyPatch,
    license_key: str | None,
    valid: bool,
    tier: str,
) -> None:
    download_module = types.ModuleType("cloakbrowser.download")
    download_module.ensure_binary = lambda: "/cache/chrome"
    download_module.binary_info = lambda: {
        "version": "148.0.0.0",
        "tier": tier,
        "platform": "linux-x64",
    }
    license_module = types.ModuleType("cloakbrowser.license")
    license_module.resolve_license_key = lambda: license_key
    license_module.validate_license = lambda key: SimpleNamespace(valid=valid)
    monkeypatch.setitem(sys.modules, "cloakbrowser.download", download_module)
    monkeypatch.setitem(sys.modules, "cloakbrowser.license", license_module)
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "0.4.11")


def test_parse_boolean_is_strict():
    assert runtime.parse_boolean("true") is True
    assert runtime.parse_boolean("off") is False
    with pytest.raises(ValueError, match="Invalid boolean"):
        runtime.parse_boolean("sometimes")


def test_parse_nonnegative_integer_rejects_negative():
    assert runtime.parse_nonnegative_integer("3") == 3
    with pytest.raises(ValueError, match="nonnegative"):
        runtime.parse_nonnegative_integer("-1")


def test_dedicated_auth_token_takes_precedence():
    assert runtime.resolve_auth_token("dedicated", "legacy") == "dedicated"
    assert runtime.resolve_auth_token(None, "legacy") == "legacy"
    assert runtime.resolve_auth_token(None, None) is None


def test_required_license_must_exist(monkeypatch: pytest.MonkeyPatch):
    _install_runtime_modules(monkeypatch, None, False, "free")
    with pytest.raises(RuntimeError, match="LICENSE_KEY is required"):
        runtime.prepare_cloakbrowser_runtime(True)


def test_required_license_must_produce_pro_binary(monkeypatch: pytest.MonkeyPatch):
    _install_runtime_modules(monkeypatch, "test-license", True, "free")
    with pytest.raises(RuntimeError, match="installed binary is free tier"):
        runtime.prepare_cloakbrowser_runtime(True)


def test_valid_pro_runtime_reports_versions(monkeypatch: pytest.MonkeyPatch):
    _install_runtime_modules(monkeypatch, "test-license", True, "pro")
    info = runtime.prepare_cloakbrowser_runtime(True)
    assert info == {
        "wrapper_version": "0.4.11",
        "binary_version": "148.0.0.0",
        "binary_tier": "pro",
        "platform": "linux-x64",
    }
