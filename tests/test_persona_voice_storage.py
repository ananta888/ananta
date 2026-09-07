"""Immutable metadata storage tests; reserved catalog identities are test-only, not grants."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.services.artifact_store import ArtifactStore
from agent.services.persona_voice_storage import PersonaVoiceStorage
from ananta_contracts.persona_voice import inspect_voice_descriptor, voice_descriptor
from tests.test_persona_asset_policy import configured as configured
from tests.test_persona_voice_catalog import voice_catalog as voice_catalog
from tests.test_persona_voice_policy import voice_policy as voice_policy


@pytest.fixture
def voice_storage(request, tmp_path):
    _, asset, _ = request.getfixturevalue("voice_catalog")
    return (
        PersonaVoiceStorage(ArtifactStore(tmp_path / "private")),
        asset,
        inspect_voice_descriptor(voice_descriptor(asset.voice_id)),
    )


def test_descriptor_storage_is_single_immutable_file_and_idempotent(voice_storage):
    storage, asset, inspected = voice_storage
    guard = Mock()
    paths = storage.write(asset, inspected, checkpoint=guard)
    assert guard.call_count == 3 and set(paths) == {asset.voice.artifact_id}
    assert next(iter(paths.values())).endswith("/v0001__voice.json")
    assert storage.write(asset, inspected, checkpoint=Mock()) == paths
    for preview in (True, False):
        assert storage.read(asset, preview=preview, checkpoint=Mock()) == inspected.descriptor


@pytest.mark.parametrize("phase", [0, 1, 2])
def test_each_write_checkpoint_stops_on_revocation(voice_storage, phase):
    storage, asset, inspected = voice_storage
    with pytest.raises(PermissionError):
        storage.write(asset, inspected, checkpoint=Mock(side_effect=[None] * phase + [PermissionError("revoked")]))
    assert len(list(storage.store.base_dir.rglob("v0001__voice.json"))) == (1 if phase == 2 else 0)


def test_changed_inspection_never_writes_and_changed_read_never_returns(voice_storage):
    storage, asset, inspected = voice_storage
    storage.store = Mock()
    with pytest.raises(ValueError):
        storage.write(asset, replace(inspected, source_sha256="0" * 64), checkpoint=Mock())
    storage.store.store_immutable_bytes.assert_not_called()
    storage.store.load_immutable_bytes.return_value = b"{}"
    with pytest.raises(ValueError):
        storage.read(asset, preview=False, checkpoint=Mock())
    storage.store.load_immutable_bytes.return_value = voice_descriptor("piper.de_DE.thorsten_emotional.medium.whisper")
    with pytest.raises(ValueError, match="storage_mismatch"):
        storage.read(asset, preview=False, checkpoint=Mock())


def test_revocation_after_read_suppresses_even_valid_descriptor(voice_storage):
    storage, asset, inspected = voice_storage
    storage.write(asset, inspected, checkpoint=Mock())
    with pytest.raises(PermissionError):
        storage.read(asset, preview=True, checkpoint=Mock(side_effect=[None, PermissionError("revoked")]))
    for preview in ("true", 1, None):
        with pytest.raises(ValueError, match="preview_flag_invalid"):
            storage.read(asset, preview=preview, checkpoint=Mock())
