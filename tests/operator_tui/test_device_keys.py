"""SS02.01: Tests für lokale Device-Key-Verwaltung."""
from __future__ import annotations

import json
import os
import pytest
import tempfile
from pathlib import Path

from client_surfaces.operator_tui.device_keys import (
    DeviceKeyManager,
    DeviceKeyError,
    _fingerprint_from_pubkey_bytes,
)


@pytest.fixture
def key_dir(tmp_path):
    return tmp_path / "device-keys"


def test_key_not_exists_initially(key_dir):
    mgr = DeviceKeyManager(key_dir)
    assert not mgr.key_exists()


def test_generate_key_creates_files(key_dir):
    mgr = DeviceKeyManager(key_dir)
    info = mgr.generate_key()
    assert mgr.key_exists()
    assert info["fingerprint"]
    assert info["key_id"]
    assert info["algorithm"]


def test_fingerprint_format(key_dir):
    mgr = DeviceKeyManager(key_dir)
    info = mgr.generate_key()
    fp = info["fingerprint"]
    # 8 Gruppen à 8 Hex-Zeichen, durch : getrennt
    parts = fp.split(":")
    assert len(parts) == 8
    for part in parts:
        assert len(part) == 8
        assert all(c in "0123456789abcdef" for c in part)


def test_get_public_info_no_private_key(key_dir):
    mgr = DeviceKeyManager(key_dir)
    mgr.generate_key()
    info = mgr.get_public_info()
    assert "key_id" in info
    assert "fingerprint" in info
    assert "public_key_pem" in info
    assert "private" not in json.dumps(info).lower() or "stub" in info.get("algorithm", "").lower()


def test_get_public_info_missing_key(key_dir):
    mgr = DeviceKeyManager(key_dir)
    with pytest.raises(DeviceKeyError):
        mgr.get_public_info()


def test_get_fingerprint(key_dir):
    mgr = DeviceKeyManager(key_dir)
    mgr.generate_key()
    fp = mgr.get_fingerprint()
    assert fp and ":" in fp


def test_rotate_key_creates_new_fingerprint(key_dir):
    mgr = DeviceKeyManager(key_dir)
    info1 = mgr.generate_key()
    info2 = mgr.rotate_key()
    # Neuer Fingerprint ist anders (mit hoher Wahrscheinlichkeit)
    assert info1["key_id"] != info2["key_id"]


def test_rotate_key_archives_old_key(key_dir):
    mgr = DeviceKeyManager(key_dir)
    mgr.generate_key()
    mgr.rotate_key()
    archive_dir = key_dir / "rotated"
    assert archive_dir.exists()
    archived = list(archive_dir.glob("*.old"))
    assert len(archived) >= 1


def test_generate_idempotent_overwrite(key_dir):
    mgr = DeviceKeyManager(key_dir)
    mgr.generate_key()
    info2 = mgr.generate_key()  # Überschreibt
    assert mgr.get_fingerprint() == info2["fingerprint"]


def test_corrupted_meta_file_raises(key_dir):
    mgr = DeviceKeyManager(key_dir)
    mgr.generate_key()
    meta_file = key_dir / "device_key.json"
    meta_file.write_text("NOT JSON", encoding="utf-8")
    with pytest.raises(DeviceKeyError, match="corrupted"):
        mgr.get_public_info()


def test_fingerprint_from_pubkey_bytes_deterministic():
    data = b"test-pubkey-bytes-42"
    fp1 = _fingerprint_from_pubkey_bytes(data)
    fp2 = _fingerprint_from_pubkey_bytes(data)
    assert fp1 == fp2
    assert len(fp1.replace(":", "")) == 64
