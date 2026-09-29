from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from voice_runtime.model_manifest import VoiceModelCatalog, load_catalog_for_config


def _write_catalog(
    tmp_path,
    *,
    revision="revision-1",
    digest=None,
    relative="model.bin",
    resources=None,
    engine="vosk",
):
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    payload = {
        "schema_version": "ananta.voice-model-catalog.v1",
        "models": [
            {
                "id": f"{engine}-de-v1",
                "engine": engine,
                "revision": revision,
                "license": "Apache-2.0",
                "quantization": "none",
                "languages": ["de"],
                **({"resources": resources} if resources is not None else {}),
                "files": [
                    {
                        "path": relative,
                        "sha256": digest or hashlib.sha256(b"model").hexdigest(),
                    }
                ],
            }
        ],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_voice_model_catalog_verifies_files_and_provenance(tmp_path):
    catalog = VoiceModelCatalog.load(_write_catalog(tmp_path))
    entry = catalog.require("vosk")

    assert entry.model_id == "vosk-de-v1"
    assert entry.provenance(device="cpu")["synthetic"] is False
    assert entry.manifest_digest.startswith("sha256:")
    assert entry.ram_bytes == len(b"model")
    entry.bind_runtime_paths((str(tmp_path / "model.bin"),))
    assert catalog.require_model("vosk-de-v1") is entry
    assert catalog.get_model("missing") is None


def test_voice_model_catalog_rejects_duplicate_model_ids(tmp_path):
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    payload = {
        "schema_version": "ananta.voice-model-catalog.v1",
        "models": [
            {
                "id": "shared-model-id",
                "engine": engine,
                "revision": "revision-1",
                "license": "Apache-2.0",
                "files": [{"path": path.name, "sha256": hashlib.sha256(content).hexdigest()}],
            }
            for engine, path, content in (
                ("vosk", first, b"first"),
                ("whisper_cpp", second, b"second"),
            )
        ],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate voice model id"):
        VoiceModelCatalog.load(catalog_path)


def test_voice_model_catalog_validates_explicit_resource_requirements(tmp_path):
    entry = VoiceModelCatalog.load(
        _write_catalog(
            tmp_path,
            resources={
                "ram_bytes": 1024,
                "vram_bytes": 2048,
                "concurrency_slots": 2,
            },
        )
    ).require("vosk")

    assert (entry.ram_bytes, entry.vram_bytes, entry.concurrency_slots) == (
        1024,
        2048,
        2,
    )


def test_voice_model_catalog_rejects_mutable_revision_digest_and_traversal(tmp_path):
    with pytest.raises(ValueError, match="immutable"):
        VoiceModelCatalog.load(_write_catalog(tmp_path, revision="latest"))
    with pytest.raises(ValueError, match="digest mismatch"):
        VoiceModelCatalog.load(_write_catalog(tmp_path, digest="0" * 64))
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        VoiceModelCatalog.load(_write_catalog(tmp_path, relative="../model.bin"))


def test_voice_model_manifest_rejects_unlisted_runtime_binary_or_model(tmp_path):
    entry = VoiceModelCatalog.load(_write_catalog(tmp_path)).require("vosk")
    unlisted = tmp_path / "unlisted.bin"
    unlisted.write_bytes(b"different model")

    with pytest.raises(ValueError, match="not covered"):
        entry.bind_runtime_paths((str(unlisted),))


def test_voice_model_manifest_rejects_unlisted_file_loaded_from_directory(tmp_path):
    model_dir = tmp_path / "model-directory"
    model_dir.mkdir()
    declared = model_dir / "model.bin"
    declared.write_bytes(b"model")
    catalog = VoiceModelCatalog.load(_write_catalog(tmp_path, relative="model-directory/model.bin"))
    (model_dir / "runtime.py").write_text("raise RuntimeError", encoding="utf-8")

    with pytest.raises(ValueError, match="not covered"):
        catalog.require("vosk").bind_runtime_paths((str(model_dir),))


def test_production_silero_model_is_manifest_bound(tmp_path):
    model_path = tmp_path / "model.bin"
    config = SimpleNamespace(
        model_manifest_path=str(_write_catalog(tmp_path, engine="silero")),
        model_root=str(tmp_path),
        production_profile=True,
        primary_backend="mock",
        asr_backend="mock",
        rerun_backend="mock",
        secondary_backends=(),
        backend_fallback_order=("mock",),
        policy_allowed_backends=(),
        vad_backend="silero",
        silero_vad_model_path=str(model_path),
    )

    catalog = load_catalog_for_config(config)

    assert catalog is not None
    assert catalog.require("silero").model_id == "silero-de-v1"


def test_production_silero_model_rejects_unmanifested_runtime_path(tmp_path):
    catalog_path = _write_catalog(tmp_path, engine="silero")
    unlisted = tmp_path / "unlisted.jit"
    unlisted.write_bytes(b"untrusted")
    config = SimpleNamespace(
        model_manifest_path=str(catalog_path),
        model_root=str(tmp_path),
        production_profile=True,
        primary_backend="mock",
        asr_backend="mock",
        rerun_backend="mock",
        secondary_backends=(),
        backend_fallback_order=("mock",),
        policy_allowed_backends=(),
        vad_backend="silero",
        silero_vad_model_path=str(unlisted),
    )

    with pytest.raises(ValueError, match="not covered"):
        load_catalog_for_config(config)
