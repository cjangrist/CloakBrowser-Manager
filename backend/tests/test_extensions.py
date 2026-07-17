"""Tests for verified extension provisioning and catalog resolution."""

from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backend import extensions


def _encode_varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_bytes(field_number: int, value: bytes) -> bytes:
    key = _encode_varint((field_number << 3) | 2)
    return key + _encode_varint(len(value)) + value


def _archive_bytes(entries: dict[str, str] | None = None) -> bytes:
    files = entries or {
        "manifest.json": json.dumps(
            {"manifest_version": 3, "name": "Verified Extension", "version": "1.2.3"}
        )
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _public_key_bytes(private_key: rsa.RSAPrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _build_crx2() -> tuple[str, bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = _public_key_bytes(private_key)
    extension_id = extensions._extension_id_from_key(public_key)
    archive = _archive_bytes()
    signature = private_key.sign(archive, padding.PKCS1v15(), hashes.SHA1())
    header = b"Cr24" + struct.pack("<III", 2, len(public_key), len(signature))
    return extension_id, header + public_key + signature + archive, archive


def _build_crx3() -> tuple[str, bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = _public_key_bytes(private_key)
    extension_id = extensions._extension_id_from_key(public_key)
    archive = _archive_bytes()
    signed_header = _protobuf_bytes(1, extensions._extension_id_bytes(extension_id))
    signed_payload = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed_header))
    signed_payload += signed_header + archive
    signature = private_key.sign(signed_payload, padding.PKCS1v15(), hashes.SHA256())
    proof = _protobuf_bytes(1, public_key) + _protobuf_bytes(2, signature)
    header = _protobuf_bytes(2, proof) + _protobuf_bytes(10000, signed_header)
    crx = b"Cr24" + struct.pack("<II", 3, len(header)) + header + archive
    return extension_id, crx, archive


def test_parse_extension_ids_deduplicates_and_preserves_order():
    first = "a" * 32
    second = "b" * 32
    assert extensions.parse_extension_ids(f"{first}, {second} {first}") == [
        first,
        second,
    ]


def test_parse_extension_ids_rejects_invalid_value():
    with pytest.raises(ValueError, match="Invalid Chrome extension IDs"):
        extensions.parse_extension_ids("not-an-extension")


def test_verify_crx2_checks_signature_and_id():
    extension_id, crx, archive = _build_crx2()
    assert extensions.verify_crx(crx, extension_id) == archive
    with pytest.raises(ValueError, match="does not match"):
        extensions.verify_crx(crx, "a" * 32)


def test_verify_crx2_rejects_oversized_header():
    crx = b"Cr24" + struct.pack("<III", 2, extensions.MAX_CRX_HEADER_BYTES + 1, 0)
    with pytest.raises(ValueError, match="header exceeds"):
        extensions.verify_crx(crx, "a" * 32)


def test_verify_crx3_checks_signature_and_signed_id():
    extension_id, crx, archive = _build_crx3()
    assert extensions.verify_crx(crx, extension_id) == archive
    verified_archive, public_key = extensions.verify_crx_package(crx, extension_id)
    assert verified_archive == archive
    assert extensions._extension_id_from_key(public_key) == extension_id
    tampered = crx[:-1] + bytes([crx[-1] ^ 1])
    with pytest.raises(ValueError, match="no valid developer proof"):
        extensions.verify_crx(tampered, extension_id)


def test_manifest_rejects_archive_traversal():
    archive = _archive_bytes(
        {
            "manifest.json": '{"name":"Unsafe","version":"1"}',
            "../outside": "blocked",
        }
    )
    with pytest.raises(ValueError, match="Unsafe extension archive entry"):
        extensions._manifest_from_archive(archive)


def test_sync_extensions_installs_and_writes_public_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    extension_id, crx, _ = _build_crx3()
    monkeypatch.setattr(extensions, "_download_crx", lambda selected, version: crx)
    catalog = extensions.sync_extensions([extension_id], tmp_path, "148.0.0.0")
    assert catalog[0]["id"] == extension_id
    assert catalog[0]["path"] == str(tmp_path / extension_id)
    installed_manifest = json.loads(
        (tmp_path / extension_id / "manifest.json").read_text()
    )
    assert extensions._manifest_key_matches_id(installed_manifest, extension_id)
    public_data = json.loads((tmp_path / "catalog.json").read_text())
    assert "path" not in public_data["extensions"][0]


def test_sync_extensions_reinstalls_same_version_when_manifest_key_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    extension_id, crx, _ = _build_crx3()
    monkeypatch.setattr(extensions, "_download_crx", lambda selected, version: crx)
    extensions.sync_extensions([extension_id], tmp_path, "148.0.0.0")
    manifest_path = tmp_path / extension_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("key")
    manifest_path.write_text(json.dumps(manifest))

    catalog = extensions.sync_extensions([extension_id], tmp_path, "148.0.0.0")

    repaired_manifest = json.loads(manifest_path.read_text())
    assert extensions._manifest_key_matches_id(repaired_manifest, extension_id)
    assert catalog[0]["cached"] is False
    assert list((tmp_path / ".trash").iterdir())


def test_extension_paths_rejects_unconfigured_id():
    with pytest.raises(ValueError, match="not configured"):
        extensions.extension_paths(["a" * 32], [])
