"""Validate CloakBrowser licensing and prepare the runtime binary."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("cloakbrowser.manager.runtime")


def parse_boolean(value: str | None, default: bool = False) -> bool:
    """Parse a strict environment boolean."""
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def parse_nonnegative_integer(value: str | None, default: int = 0) -> int:
    """Parse a nonnegative integer environment value."""
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed < 0:
        raise ValueError("Value must be nonnegative")
    return parsed


def resolve_auth_token(
    dedicated_token: str | None,
    legacy_token: str | None,
) -> str | None:
    """Prefer the deployment-specific token while retaining compatibility."""
    return dedicated_token or legacy_token or None


def runtime_settings() -> dict[str, Any]:
    """Load runtime settings from the process environment."""
    return {
        "require_license": parse_boolean(os.environ.get("CLOAKBROWSER_REQUIRE_LICENSE")),
        "max_running_profiles": parse_nonnegative_integer(
            os.environ.get("MAX_RUNNING_PROFILES")
        ),
        "extension_ids_raw": os.environ.get("CLOAKBROWSER_EXTENSION_IDS"),
        "extensions_root": os.environ.get("CLOAKBROWSER_EXTENSIONS_DIR", "/data/extensions"),
        "downloads_root": os.environ.get("CLOAKBROWSER_DOWNLOADS_DIR", "/data/downloads"),
        "fonts_dir": os.environ.get("CLOAKBROWSER_FONTS_DIR", "/data/fonts/windows"),
    }


def prepare_cloakbrowser_runtime(require_license: bool) -> dict[str, Any]:
    """Validate entitlement, install the official binary, and report its tier."""
    import importlib.metadata

    from cloakbrowser.download import binary_info, ensure_binary
    from cloakbrowser.license import resolve_license_key, validate_license

    license_key = resolve_license_key()
    if require_license and not license_key:
        raise RuntimeError("CLOAKBROWSER_LICENSE_KEY is required")
    license_info = validate_license(license_key) if license_key else None
    if require_license and (not license_info or not license_info.valid):
        raise RuntimeError("CloakBrowser Pro license validation failed")
    ensure_binary()
    info = binary_info()
    if require_license and info.get("tier") != "pro":
        raise RuntimeError("CloakBrowser Pro was required but the installed binary is free tier")
    result = {
        "wrapper_version": importlib.metadata.version("cloakbrowser"),
        "binary_version": str(info.get("version") or "unknown"),
        "binary_tier": str(info.get("tier") or "unknown"),
        "platform": str(info.get("platform") or "unknown"),
    }
    logger.info(
        "Prepared CloakBrowser wrapper=%s binary=%s tier=%s platform=%s",
        result["wrapper_version"],
        result["binary_version"],
        result["binary_tier"],
        result["platform"],
    )
    return result
