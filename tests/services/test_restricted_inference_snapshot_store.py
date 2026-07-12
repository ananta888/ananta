from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from agent.services.restricted_inference_contract import RestrictedInferenceOperation
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    ROLE_WEIGHTS,
    SOURCE_HUGGINGFACE_SNAPSHOT,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    ModelManifestValidationError,
    RestrictedModelManifest,
)
from agent.services.restricted_inference_snapshot_store import (
    RemoteSnapshotPolicy,
    SecureSnapshotStore,
    SnapshotDownloadError,
)
from worker.runtime.restricted_inference_admission import FilesystemSnapshotAdmission


def _manifest(
    content: bytes = b"weights",
    *,
    source_type: str = SOURCE_LOCAL_SNAPSHOT,
) -> RestrictedModelManifest:
    return RestrictedModelManifest(
        manifest_id="fixture-manifest-v1",
        model_id="fixture/model",
        engine=ENGINE_HUGGINGFACE,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef",
        source_type=source_type,
        license_id="Apache-2.0",
        operations=(RestrictedInferenceOperation.CLASSIFY,),
        files=(
            ModelManifestFile(
                "model.safetensors",
                hashlib.sha256(content).hexdigest(),
                len(content),
                ROLE_WEIGHTS,
            ),
        ),
    )


def test_local_snapshot_is_verified_promoted_by_digest_and_admitted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.safetensors").write_bytes(b"weights")
    snapshots = tmp_path / "snapshots"
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest = _manifest()
    (manifests / f"{manifest.manifest_id}.json").write_text(
        json.dumps(manifest.to_dict()),
        encoding="utf-8",
    )

    promoted = SecureSnapshotStore(snapshot_root=snapshots).import_local(source, manifest)
    admitted = FilesystemSnapshotAdmission(
        manifest_root=manifests,
        snapshot_root=snapshots,
    ).admit(manifest.manifest_id)

    assert promoted.root == snapshots / manifest.digest
    assert admitted.manifest_digest == manifest.digest
    assert admitted.manifest is not None
    assert admitted.manifest.device == "cpu"


def test_admission_rejects_tampering_after_promotion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.safetensors").write_bytes(b"weights")
    snapshots = tmp_path / "snapshots"
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest = _manifest()
    SecureSnapshotStore(snapshot_root=snapshots).import_local(source, manifest)
    (manifests / f"{manifest.manifest_id}.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    (snapshots / manifest.digest / "model.safetensors").write_bytes(b"tampered")

    with pytest.raises(ModelManifestValidationError) as error:
        FilesystemSnapshotAdmission(manifest_root=manifests, snapshot_root=snapshots).admit(manifest.manifest_id)

    assert error.value.reason_code in {"hash_mismatch", "size_mismatch"}


def test_remote_download_is_default_deny_and_blocks_private_dns(tmp_path: Path) -> None:
    disabled = SecureSnapshotStore(snapshot_root=tmp_path / "disabled")
    with pytest.raises(SnapshotDownloadError) as denied:
        disabled.download(base_url="https://models.example/model", manifest=_manifest(), authorized=True)
    assert denied.value.reason_code == "remote_download_denied"

    def private_resolver(host: str, port: int, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    store = SecureSnapshotStore(
        snapshot_root=tmp_path / "private",
        remote_policy=RemoteSnapshotPolicy(enabled=True, allowed_hosts=frozenset({"models.example"})),
        resolver=private_resolver,
    )
    with pytest.raises(SnapshotDownloadError) as blocked:
        store.validate_remote_url("https://models.example/model")
    assert blocked.value.reason_code == "remote_address_forbidden"


class _Response:
    def __init__(self, content: bytes, *, peer_ip: str = "93.184.216.34") -> None:
        self._content = content
        self._offset = 0
        self.headers = {"Content-Length": str(len(content))}
        self.peer_ip = peer_ip

    def read(self, count: int) -> bytes:
        chunk = self._content[self._offset : self._offset + count]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None


class _Opener:
    def __init__(self, content: bytes, *, peer_ip: str = "93.184.216.34") -> None:
        self.content = content
        self.peer_ip = peer_ip

    def open(self, _request, timeout: float):
        assert timeout > 0
        return _Response(self.content, peer_ip=self.peer_ip)


def _public_resolver(host: str, port: int, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_failed_download_leaves_no_promotable_or_partial_snapshot(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    store = SecureSnapshotStore(
        snapshot_root=snapshots,
        remote_policy=RemoteSnapshotPolicy(enabled=True, allowed_hosts=frozenset({"models.example"})),
        resolver=_public_resolver,
        opener=_Opener(b"tampered"),
    )
    manifest = _manifest(b"expected", source_type=SOURCE_HUGGINGFACE_SNAPSHOT)

    with pytest.raises(SnapshotDownloadError) as error:
        store.download(
            base_url="https://models.example/immutable-revision",
            manifest=manifest,
            authorized=True,
        )

    assert error.value.reason_code in {"hash_mismatch", "size_mismatch"}
    assert not (snapshots / manifest.digest).exists()
    assert not list(snapshots.glob(".restricted-download-*"))


def test_remote_download_rejects_dns_rebinding_peer(tmp_path: Path) -> None:
    store = SecureSnapshotStore(
        snapshot_root=tmp_path / "snapshots",
        remote_policy=RemoteSnapshotPolicy(enabled=True, allowed_hosts=frozenset({"models.example"})),
        resolver=_public_resolver,
        opener=_Opener(b"weights", peer_ip="93.184.216.35"),
    )

    with pytest.raises(SnapshotDownloadError) as error:
        store.download(
            base_url="https://models.example/immutable-revision",
            manifest=_manifest(source_type=SOURCE_HUGGINGFACE_SNAPSHOT),
            authorized=True,
        )

    assert error.value.reason_code == "dns_rebinding_detected"
    assert not list((tmp_path / "snapshots").glob(".restricted-download-*"))
