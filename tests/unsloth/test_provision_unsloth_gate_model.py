from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from scripts.provision_unsloth_gate_model import (
    SMOLLM2_135M_INSTRUCT,
    ModelProvisioningError,
    PinnedFile,
    PinnedSnapshot,
    provision,
)

_PAYLOADS = {"config.json": b'{"model_type":"llama"}', "model.safetensors": b"weights"}
_SNAPSHOT = PinnedSnapshot(
    repository="example/tiny",
    revision="a" * 40,
    license="Apache-2.0",
    files=tuple(
        PinnedFile(name, hashlib.sha256(payload).hexdigest(), len(payload)) for name, payload in _PAYLOADS.items()
    ),
)


def _fetcher(payloads: dict[str, bytes]):
    def fetch(url: str) -> io.BytesIO:
        return io.BytesIO(payloads[url.rsplit("/", 1)[-1]])

    return fetch


def test_default_snapshot_is_pinned_to_an_immutable_revision() -> None:
    assert len(SMOLLM2_135M_INSTRUCT.revision) == 40
    assert {item.name for item in SMOLLM2_135M_INSTRUCT.files} >= {"config.json", "model.safetensors", "tokenizer.json"}
    assert all(len(item.sha256) == 64 and item.size_bytes > 0 for item in SMOLLM2_135M_INSTRUCT.files)


def test_provision_materializes_verified_files_atomically_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "tiny-causal-lm"

    assert provision(target, snapshot=_SNAPSHOT, fetcher=_fetcher(_PAYLOADS)) == "created"
    assert (target / "model.safetensors").read_bytes() == b"weights"
    assert provision(target, snapshot=_SNAPSHOT, fetcher=_fetcher({})) == "verified"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tiny-causal-lm"]


def test_provision_rejects_tampered_download_without_exposing_target(tmp_path: Path) -> None:
    target = tmp_path / "tiny-causal-lm"
    tampered = {**_PAYLOADS, "model.safetensors": b"weightz"}

    with pytest.raises(ModelProvisioningError, match="model_provisioning_digest_mismatch"):
        provision(target, snapshot=_SNAPSHOT, fetcher=_fetcher(tampered))
    assert list(tmp_path.iterdir()) == []


def test_provision_rejects_existing_directory_with_unpinned_content(tmp_path: Path) -> None:
    target = tmp_path / "tiny-causal-lm"
    target.mkdir()
    (target / "config.json").write_bytes(_PAYLOADS["config.json"])

    with pytest.raises(ModelProvisioningError, match="model_provisioning_file_set_mismatch"):
        provision(target, snapshot=_SNAPSHOT, fetcher=_fetcher(_PAYLOADS))


def test_provision_requires_the_matrix_approved_basename(tmp_path: Path) -> None:
    with pytest.raises(ModelProvisioningError, match="model_provisioning_basename_not_approved"):
        provision(tmp_path / "other-model", snapshot=_SNAPSHOT, fetcher=_fetcher(_PAYLOADS))
