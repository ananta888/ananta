from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.services.codecompass_graph_artifact_resolver import (
    CodeCompassGraphArtifactResolver,
)


def _index(path: Path, *, digest: str):
    return SimpleNamespace(
        output_dir=None,
        index_metadata={
            "graph_artifacts": {
                "schema": "codecompass_graph_artifact_binding.v1",
                "graph_revision": "sha256:" + "a" * 64,
                "graph_index": {
                    "artifact_schema": "codecompass_graph_index.v1",
                    "filename": "cc_graph_index.json",
                    "sha256": digest,
                    "local_path": str(path),
                },
            }
        },
    )


def test_admitted_graph_must_remain_under_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "indices"
    root.mkdir()
    outside = tmp_path / "cc_graph_index.json"
    outside.write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    resolver = CodeCompassGraphArtifactResolver(
        artifact_root=root,
        allow_legacy=False,
    )

    with pytest.raises(ValueError, match="outside_root"):
        resolver.resolve(_index(outside, digest=digest))


def test_hash_drift_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "indices"
    path = root / "index" / "run" / "cc_graph_index.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": {}}), encoding="utf-8")
    resolver = CodeCompassGraphArtifactResolver(
        artifact_root=root,
        allow_legacy=False,
    )

    with pytest.raises(ValueError, match="hash_drift"):
        resolver.resolve(_index(path, digest="b" * 64))


def test_legacy_resolution_can_be_disabled(tmp_path: Path) -> None:
    root = tmp_path / "indices"
    path = root / "legacy" / "cc_graph_index.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    index = SimpleNamespace(
        output_dir=str(path.parent),
        index_metadata={},
    )

    with pytest.raises(ValueError, match="legacy.*disabled"):
        CodeCompassGraphArtifactResolver(
            artifact_root=root,
            allow_legacy=False,
        ).resolve(index)
