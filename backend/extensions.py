"""Provision verified Chrome Web Store extensions for browser profiles."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import logging
import os
import re
import stat
import struct
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

logger = logging.getLogger("cloakbrowser.manager.extensions")

CHROME_WEB_STORE_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")
MAX_CRX_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_CRX_HEADER_BYTES = 1 << 18


def parse_extension_ids(raw_value: str | None) -> list[str]:
    """Parse a comma or whitespace separated Chrome extension ID list."""
    values = re.split(r"[\s,]+", raw_value.strip()) if raw_value else []
    extension_ids = list(dict.fromkeys(value for value in values if value))
    invalid = [
        value for value in extension_ids if not EXTENSION_ID_PATTERN.fullmatch(value)
    ]
    if invalid:
        raise ValueError(f"Invalid Chrome extension IDs: {', '.join(invalid)}")
    return extension_ids


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        current = data[offset]
        offset += 1
        value |= (current & 0x7F) << shift
        if current < 0x80:
            return value, offset
        shift += 7
    raise ValueError("Malformed protobuf varint")


def _parse_protobuf(data: bytes) -> dict[int, list[bytes | int]]:
    fields: dict[int, list[bytes | int]] = {}
    offset = 0
    while offset < len(data):
        key, offset = _decode_varint(data, offset)
        field_number, wire_type = key >> 3, key & 7
        if field_number == 0:
            raise ValueError("Malformed protobuf field number")
        if wire_type == 0:
            value, offset = _decode_varint(data, offset)
        elif wire_type == 2:
            length, offset = _decode_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError("Truncated protobuf field")
            value, offset = data[offset:end], end
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
        fields.setdefault(field_number, []).append(value)
    return fields


def _extension_id_from_key(public_key: bytes) -> str:
    digest = hashlib.sha256(public_key).digest()[:16]
    return "".join(
        chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 0x0F)
    )


def _extension_id_bytes(extension_id: str) -> bytes:
    nibbles = [ord(character) - ord("a") for character in extension_id]
    return bytes((high << 4) | low for high, low in zip(nibbles[::2], nibbles[1::2]))


def _verify_crx2(data: bytes, extension_id: str) -> tuple[bytes, bytes]:
    if len(data) < 16:
        raise ValueError("Truncated CRX2 header")
    public_key_size, signature_size = struct.unpack_from("<II", data, 8)
    if public_key_size + signature_size > MAX_CRX_HEADER_BYTES:
        raise ValueError("CRX2 header exceeds the allowed size")
    archive_offset = 16 + public_key_size + signature_size
    if archive_offset > len(data):
        raise ValueError("Truncated CRX2 payload")
    public_key = data[16 : 16 + public_key_size]
    signature = data[16 + public_key_size : archive_offset]
    archive = data[archive_offset:]
    if _extension_id_from_key(public_key) != extension_id:
        raise ValueError("CRX2 public key does not match requested extension ID")
    loaded_key = serialization.load_der_public_key(public_key)
    if not isinstance(loaded_key, rsa.RSAPublicKey):
        raise ValueError("CRX2 public key is not RSA")
    loaded_key.verify(signature, archive, padding.PKCS1v15(), hashes.SHA1())
    return archive, public_key


def _extract_crx3_parts(
    data: bytes,
) -> tuple[dict[int, list[bytes | int]], bytes, bytes]:
    if len(data) < 12:
        raise ValueError("Truncated CRX3 header")
    header_size = struct.unpack_from("<I", data, 8)[0]
    if header_size > MAX_CRX_HEADER_BYTES:
        raise ValueError("CRX3 header exceeds the allowed size")
    archive_offset = 12 + header_size
    if archive_offset > len(data):
        raise ValueError("Truncated CRX3 payload")
    header = _parse_protobuf(data[12:archive_offset])
    signed_values = header.get(10000, [])
    if len(signed_values) != 1 or not isinstance(signed_values[0], bytes):
        raise ValueError("CRX3 signed header is missing")
    return header, signed_values[0], data[archive_offset:]


def _verify_crx3_proof(
    proof_data: bytes,
    signed_payload: bytes,
    extension_id: str,
    algorithm: str,
) -> bytes | None:
    proof = _parse_protobuf(proof_data)
    public_values, signature_values = proof.get(1, []), proof.get(2, [])
    if len(public_values) != 1 or len(signature_values) != 1:
        return None
    public_key, signature = public_values[0], signature_values[0]
    if not isinstance(public_key, bytes) or not isinstance(signature, bytes):
        return None
    if _extension_id_from_key(public_key) != extension_id:
        return None
    try:
        loaded_key = serialization.load_der_public_key(public_key)
        if algorithm == "rsa" and isinstance(loaded_key, rsa.RSAPublicKey):
            loaded_key.verify(
                signature, signed_payload, padding.PKCS1v15(), hashes.SHA256()
            )
            return public_key
        if algorithm == "ecdsa" and isinstance(loaded_key, ec.EllipticCurvePublicKey):
            loaded_key.verify(signature, signed_payload, ec.ECDSA(hashes.SHA256()))
            return public_key
    except (InvalidSignature, TypeError, ValueError):
        return None
    return None


def _verify_crx3(data: bytes, extension_id: str) -> tuple[bytes, bytes]:
    header, signed_header, archive = _extract_crx3_parts(data)
    signed_data = _parse_protobuf(signed_header)
    declared_ids = signed_data.get(1, [])
    if declared_ids != [_extension_id_bytes(extension_id)]:
        raise ValueError("CRX3 signed ID does not match requested extension ID")
    signed_payload = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed_header))
    signed_payload += signed_header + archive
    proofs = [(value, "rsa") for value in header.get(2, [])]
    proofs += [(value, "ecdsa") for value in header.get(3, [])]
    public_key = next(
        (
            verified_key
            for proof, algorithm in proofs
            if isinstance(proof, bytes)
            and (
                verified_key := _verify_crx3_proof(
                    proof, signed_payload, extension_id, algorithm
                )
            )
        ),
        None,
    )
    if public_key is None:
        raise ValueError(
            "CRX3 has no valid developer proof for the requested extension ID"
        )
    return archive, public_key


def verify_crx_package(data: bytes, extension_id: str) -> tuple[bytes, bytes]:
    """Verify a CRX package and return its archive and developer public key."""
    if len(data) > MAX_CRX_BYTES:
        raise ValueError("CRX package exceeds the allowed size")
    if data[:4] != b"Cr24" or len(data) < 8:
        raise ValueError("Downloaded file is not a CRX package")
    version = struct.unpack_from("<I", data, 4)[0]
    if version == 2:
        return _verify_crx2(data, extension_id)
    if version == 3:
        return _verify_crx3(data, extension_id)
    raise ValueError(f"Unsupported CRX version {version}")


def verify_crx(data: bytes, extension_id: str) -> bytes:
    """Verify a CRX2/CRX3 package and return its ZIP archive bytes."""
    archive, _ = verify_crx_package(data, extension_id)
    return archive


def _validate_archive(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("Extension archive has too many entries")
    total_size = sum(entry.file_size for entry in entries)
    if total_size > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Extension archive exceeds the uncompressed size limit")
    for entry in entries:
        path = PurePosixPath(entry.filename)
        mode = entry.external_attr >> 16
        if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(mode):
            raise ValueError(f"Unsafe extension archive entry: {entry.filename}")


def _manifest_from_archive(archive_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        _validate_archive(archive)
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Extension archive has no valid manifest.json") from exc
    if not isinstance(manifest, dict) or not manifest.get("version"):
        raise ValueError("Extension manifest has no version")
    return manifest


def _download_crx(extension_id: str, browser_version: str) -> bytes:
    params = {
        "response": "redirect",
        "prodversion": browser_version,
        "acceptformat": "crx2,crx3",
        "x": f"id={extension_id}&installsource=ondemand&uc",
    }
    headers = {"User-Agent": f"Mozilla/5.0 Chrome/{browser_version}"}
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        with client.stream(
            "GET", CHROME_WEB_STORE_UPDATE_URL, params=params, headers=headers
        ) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", "0"))
            if content_length > MAX_CRX_BYTES:
                raise ValueError("CRX package exceeds the allowed size")
            package = bytearray()
            for chunk in response.iter_bytes():
                if len(package) + len(chunk) > MAX_CRX_BYTES:
                    raise ValueError("CRX package exceeds the allowed size")
                package.extend(chunk)
            return bytes(package)


def _resolve_manifest_name(extension_path: Path, manifest: dict[str, Any]) -> str:
    name = str(
        manifest.get("short_name") or manifest.get("name") or extension_path.name
    )
    match = re.fullmatch(r"__MSG_(.+)__", name)
    if not match:
        return name
    locale = str(manifest.get("default_locale") or "en")
    messages_path = extension_path / "_locales" / locale / "messages.json"
    try:
        messages = json.loads(messages_path.read_text())
        message = messages.get(match.group(1), {}).get("message")
        return str(message or extension_path.name)
    except (OSError, json.JSONDecodeError, AttributeError):
        return extension_path.name


def _catalog_entry(
    extension_id: str, extension_path: Path, cached: bool
) -> dict[str, Any]:
    manifest = json.loads((extension_path / "manifest.json").read_text())
    if not _manifest_key_matches_id(manifest, extension_id):
        raise ValueError(f"Installed extension key does not match {extension_id}")
    return {
        "id": extension_id,
        "name": _resolve_manifest_name(extension_path, manifest),
        "version": str(manifest["version"]),
        "path": str(extension_path),
        "default": True,
        "source": "chrome_web_store",
        "cached": cached,
    }


def _manifest_key_matches_id(manifest: dict[str, Any], extension_id: str) -> bool:
    encoded_key = manifest.get("key")
    if not isinstance(encoded_key, str):
        return False
    try:
        public_key = base64.b64decode(encoded_key, validate=True)
    except (binascii.Error, ValueError):
        return False
    return _extension_id_from_key(public_key) == extension_id


def _write_verified_manifest_key(
    staging: Path,
    manifest: dict[str, Any],
    public_key: bytes,
) -> None:
    verified_manifest = dict(manifest)
    verified_manifest["key"] = base64.b64encode(public_key).decode("ascii")
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(json.dumps(verified_manifest, indent=2) + "\n")


def _install_archive(
    extension_id: str,
    archive_bytes: bytes,
    manifest: dict[str, Any],
    public_key: bytes,
    extensions_root: Path,
) -> dict[str, Any]:
    extension_path = extensions_root / extension_id
    current_manifest_path = extension_path / "manifest.json"
    current_manifest: dict[str, Any] = {}
    if current_manifest_path.exists():
        current_manifest = json.loads(current_manifest_path.read_text())
        current_version_matches = current_manifest.get("version") == manifest.get(
            "version"
        )
        if current_version_matches and _manifest_key_matches_id(
            current_manifest, extension_id
        ):
            return _catalog_entry(extension_id, extension_path, cached=True)
    staging = extensions_root / ".staging" / f"{extension_id}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            _validate_archive(archive)
            archive.extractall(staging)
        _write_verified_manifest_key(staging, manifest, public_key)
    except Exception:
        trash = extensions_root / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        os.replace(staging, trash / f"failed-{extension_id}-{uuid.uuid4().hex}")
        raise
    if extension_path.exists():
        trash = extensions_root / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        old_version = str(current_manifest.get("version", "unknown"))
        old_name = f"{extension_id}-{old_version}-{int(time.time())}-{uuid.uuid4().hex}"
        os.replace(extension_path, trash / old_name)
    os.replace(staging, extension_path)
    return _catalog_entry(extension_id, extension_path, cached=False)


def _provision_extension(
    extension_id: str,
    extensions_root: Path,
    browser_version: str,
) -> dict[str, Any]:
    extension_path = extensions_root / extension_id
    try:
        archive_bytes, public_key = verify_crx_package(
            _download_crx(extension_id, browser_version), extension_id
        )
        manifest = _manifest_from_archive(archive_bytes)
        entry = _install_archive(
            extension_id, archive_bytes, manifest, public_key, extensions_root
        )
        logger.info(
            "Provisioned extension %s version %s", extension_id, entry["version"]
        )
        return entry
    except Exception as exc:
        if (extension_path / "manifest.json").exists():
            logger.warning(
                "Using cached extension %s after update failure: %s", extension_id, exc
            )
            return _catalog_entry(extension_id, extension_path, cached=True)
        raise RuntimeError(
            f"Failed to provision extension {extension_id}: {exc}"
        ) from exc


def _write_catalog(entries: list[dict[str, Any]], extensions_root: Path) -> None:
    public_entries = [
        {key: value for key, value in entry.items() if key != "path"}
        for entry in entries
    ]
    catalog_path = extensions_root / "catalog.json"
    temporary_path = extensions_root / f".catalog-{uuid.uuid4().hex}.tmp"
    temporary_path.write_text(json.dumps({"extensions": public_entries}, indent=2))
    os.replace(temporary_path, catalog_path)


def _archive_stale_staging(extensions_root: Path) -> None:
    staging_root = extensions_root / ".staging"
    trash_root = extensions_root / ".trash"
    staging_root.mkdir(parents=True, exist_ok=True)
    stale_entries = list(staging_root.iterdir())
    if not stale_entries:
        return
    trash_root.mkdir(parents=True, exist_ok=True)
    for entry in stale_entries:
        os.replace(entry, trash_root / f"stale-{entry.name}-{uuid.uuid4().hex}")


def sync_extensions(
    extension_ids: list[str],
    extensions_root: Path,
    browser_version: str,
) -> list[dict[str, Any]]:
    """Download, verify, unpack, and catalog configured extensions."""
    extensions_root.mkdir(parents=True, exist_ok=True)
    _archive_stale_staging(extensions_root)
    entries = [
        _provision_extension(extension_id, extensions_root, browser_version)
        for extension_id in extension_ids
    ]
    _write_catalog(entries, extensions_root)
    return entries


def public_catalog(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return catalog metadata without container filesystem paths."""
    return [
        {key: value for key, value in entry.items() if key != "path"}
        for entry in entries
    ]


def extension_paths(
    selected_ids: list[str],
    catalog: list[dict[str, Any]],
) -> list[str]:
    """Resolve selected configured IDs to verified unpacked directories."""
    paths_by_id = {entry["id"]: entry["path"] for entry in catalog}
    unknown = [
        extension_id for extension_id in selected_ids if extension_id not in paths_by_id
    ]
    if unknown:
        raise ValueError(f"Extensions are not configured: {', '.join(unknown)}")
    return [paths_by_id[extension_id] for extension_id in selected_ids]
