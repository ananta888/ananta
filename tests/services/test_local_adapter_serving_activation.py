from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent.services.local_adapter_serving_activation import (
    LocalAdapterCandidateSource,
    LocalAdapterServingActivationService,
    LocalAdapterServingProjection,
    SubprocessLocalAdapterServingMaterializer,
)


def _tree_digest(root: Path) -> str:
    rows = [
        {
            "name": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class _Sources:
    def __init__(self, source):
        self.source = source
        self.calls = []

    def resolve(self, **values):
        self.calls.append(values)
        return self.source


def _materializer(tmp_path: Path, runner):
    needle = tmp_path / "needle"
    needle.write_text("binary")
    needle.chmod(0o700)
    needle_base = tmp_path / "needle.pkl"
    needle_base.write_bytes(b"base")
    lfm_python = tmp_path / "python"
    lfm_python.write_text("binary")
    lfm_python.chmod(0o700)
    lfm_converter = tmp_path / "convert.py"
    lfm_converter.write_text("converter")
    lfm_base = tmp_path / "lfm-base"
    lfm_base.mkdir()
    return SubprocessLocalAdapterServingMaterializer(
        output_root=tmp_path / "serving",
        needle_binary=needle,
        needle_base_checkpoint=needle_base,
        lfm_python=lfm_python,
        lfm_converter=lfm_converter,
        lfm_base_snapshot=lfm_base,
        runner=runner,
    )


def test_needle_candidate_is_materialized_and_projected_atomically(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "adapter.pkl").write_bytes(b"adapter")
    digest = _tree_digest(candidate)

    def runner(command, **_kwargs):
        Path(command[command.index("--out") + 1]).write_bytes(b"serving-cact")

    source = LocalAdapterCandidateSource("candidate-1", "needle2", candidate, digest)
    projection = LocalAdapterServingProjection(tmp_path / "state" / "active-adapters.v1.json")
    service = LocalAdapterServingActivationService(
        sources=_Sources(source),
        materializer=_materializer(tmp_path, runner),
        projection=projection,
    )

    previous = service.switch(target="needle2", candidate_id="candidate-1", candidate_sha256=digest)
    active = projection.active("needle2")

    assert previous is None
    assert active is not None
    assert active.candidate_sha256 == digest
    assert Path(active.serving_path).read_bytes() == b"serving-cact"
    assert json.loads((tmp_path / "state" / "active-adapters.v1.json").read_text())["targets"]["needle2"]


def test_materialization_rejects_candidate_tree_tampering(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "adapter.pkl").write_bytes(b"tampered")
    source = LocalAdapterCandidateSource("candidate-1", "needle2", candidate, "a" * 64)

    with pytest.raises(ValueError, match="candidate_digest_mismatch"):
        _materializer(tmp_path, lambda *_args, **_kwargs: None).materialize(source)


def test_projection_rejects_serving_artifact_tampering(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "adapter.pkl").write_bytes(b"adapter")
    digest = _tree_digest(candidate)

    def runner(command, **_kwargs):
        Path(command[command.index("--out") + 1]).write_bytes(b"serving")

    projection = LocalAdapterServingProjection(tmp_path / "state" / "active-adapters.v1.json")
    service = LocalAdapterServingActivationService(
        sources=_Sources(LocalAdapterCandidateSource("candidate-1", "needle2", candidate, digest)),
        materializer=_materializer(tmp_path, runner),
        projection=projection,
    )
    service.switch(target="needle2", candidate_id="candidate-1", candidate_sha256=digest)
    Path(projection.active("needle2").serving_path).write_bytes(b"tampered")  # type: ignore[union-attr]

    with pytest.raises(RuntimeError, match="artifact_digest_mismatch"):
        projection.active("needle2")


def test_base_switch_removes_target_without_human_gate(tmp_path) -> None:
    projection = LocalAdapterServingProjection(tmp_path / "active.json")
    service = LocalAdapterServingActivationService(
        sources=_Sources(None),
        materializer=None,  # type: ignore[arg-type]
        projection=projection,
    )

    assert service.switch(target="needle2", candidate_id=None, candidate_sha256=None) is None
    assert projection.active("needle2") is None
