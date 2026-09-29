from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_execution import ConfiguredVectorIndexExecution
from worker.retrieval.vector_index_input_loader import (
    BoundedVectorIndexInputLoader,
)


def _scope(workspace_id: str = "workspace-a") -> dict[str, str]:
    return {
        "workspace_id": workspace_id,
        "repository_id": "repository-a",
        "profile_name": "default",
        "domain": "codecompass",
    }


def _write_input_ref(
    root: Path,
    raw: bytes,
    *,
    scope: dict[str, str] | None = None,
) -> dict[str, str]:
    reference = VectorIndexArtifactLocator.locate(
        scope=scope or _scope(),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    ).to_reference()
    target = root / reference["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return reference


def _resolved(index_path: Path) -> dict[str, object]:
    return {
        "schema": "ananta.vector_store_resolved_config.v1",
        "config": {
            "provider": "json",
            "json": {"index_path": str(index_path)},
        },
    }


def test_index_upsert_does_not_require_compatibility(tmp_path: Path) -> None:
    execution = ConfiguredVectorIndexExecution()
    result = execution.execute(
        operation="index",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "points": [
                {
                    "record_id": "record-a",
                    "vector": [1.0, 0.0],
                    "payload": {"kind": "code", "file": "src/a.py"},
                    "source_hash": "source-a",
                }
            ]
        },
        idempotency_key="index-idempotency-key",
    )

    assert result["status"] == "completed"
    assert result["reason_code"] == "upsert"
    assert result["result"]["upserted"] == 1
    assert len(result["result"]["idempotency_key_hash"]) == 64


def test_worker_rejects_point_scope_override(tmp_path: Path) -> None:
    execution = ConfiguredVectorIndexExecution()
    result = execution.execute(
        operation="index",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "points": [
                {
                    "record_id": "record-a",
                    "vector": [1.0, 0.0],
                    "scope": {
                        "workspace_id": "workspace-b",
                        "repository_id": "repository-a",
                        "profile_name": "default",
                        "domain": "codecompass",
                    },
                }
            ]
        },
        idempotency_key="scope-idempotency-key",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "vector_index_point_scope_mismatch"
    assert result["result"] is None


def test_worker_loads_bounded_input_ref_and_rejects_traversal(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    raw = json.dumps(
        {
            "points": [
                {
                    "record_id": "record-a",
                    "vector": [1.0, 0.0],
                    "payload": {"kind": "code"},
                    "source_hash": "source-a",
                }
            ]
        }
    ).encode("utf-8")
    input_ref = _write_input_ref(input_root, raw)
    wrong_digest_ref = VectorIndexArtifactLocator.locate(
        scope=_scope(),
        content_sha256="0" * 64,
    ).to_reference()
    wrong_digest_path = input_root / wrong_digest_ref["path"]
    wrong_digest_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_digest_path.write_bytes(raw)
    execution = ConfiguredVectorIndexExecution(
        input_loader=BoundedVectorIndexInputLoader(
            allowed_roots=(input_root,)
        )
    )
    kwargs = {
        "operation": "index",
        "scope": _scope(),
        "resolved_config": _resolved(tmp_path / "index.json"),
        "idempotency_key": "input-ref-idempotency",
    }

    completed = execution.execute(
        **kwargs,
        payload={
            "input_ref": input_ref
        },
    )
    missing_hash = execution.execute(
        **kwargs,
        payload={"input_ref": {"path": input_ref["path"]}},
    )
    wrong_hash = execution.execute(
        **kwargs,
        payload={
            "input_ref": wrong_digest_ref,
        },
    )
    rejected = execution.execute(
        **kwargs,
        payload={
            "input_ref": {
                **input_ref,
                "path": "../points.json",
            }
        },
    )

    assert completed["status"] == "completed"
    assert completed["result"]["upserted"] == 1
    assert (
        missing_hash["reason_code"]
        == "vector_index_input_ref_sha256_required"
    )
    assert (
        wrong_hash["reason_code"]
        == "vector_index_input_ref_digest_mismatch"
    )
    assert rejected["reason_code"] == "vector_index_input_ref_path_invalid"


def test_worker_rejects_cross_scope_and_legacy_migration_before_execution(
    tmp_path: Path,
) -> None:
    calls = {"store": 0, "prepare": 0}

    class PreparationSpy:
        def prepare(self, **_kwargs):
            calls["prepare"] += 1
            raise AssertionError("embedding preparation must not run")

    class Execution(ConfiguredVectorIndexExecution):
        def _create_store(self, config):
            calls["store"] += 1
            raise AssertionError("store creation must not run")

    execution = Execution(
        input_loader=BoundedVectorIndexInputLoader(
            allowed_roots=(tmp_path,)
        ),
        preparation_service=PreparationSpy(),
    )
    raw = b'{"schema":"ananta.vector_index_documents.v1","kind":"codecompass_documents","documents":[{}]}'
    cross_scope_ref = VectorIndexArtifactLocator.locate(
        scope=_scope("workspace-b"),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    ).to_reference()
    cross_scope = execution.execute(
        operation="refresh",
        scope=_scope("workspace-a"),
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "input_ref": cross_scope_ref,
            "preparation": {"kind": "codecompass_documents"},
        },
        idempotency_key="cross-scope-input-ref",
    )
    legacy_migration = execution.execute(
        operation="migrate",
        scope=_scope(),
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "migration": {
                "dry_run": True,
                "source_path": "legacy.json",
            }
        },
        idempotency_key="legacy-source-path",
    )

    assert cross_scope["reason_code"] == (
        "vector_index_input_ref_scope_mismatch"
    )
    assert legacy_migration["reason_code"] == (
        "vector_index_migration_fields_forbidden"
    )
    assert calls == {"store": 0, "prepare": 0}


def test_worker_generates_embeddings_from_digest_bound_document_input(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    raw = json.dumps(
        {
            "schema": "ananta.vector_index_documents.v1",
            "kind": "codecompass_documents",
            "documents": [
                {
                    "record_id": "record-a",
                    "kind": "python_function",
                    "file": "src/a.py",
                    "embedding_text": "payment timeout",
                    "source_hash": "source-a",
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    input_ref = _write_input_ref(input_root, raw)
    execution = ConfiguredVectorIndexExecution(
        input_loader=BoundedVectorIndexInputLoader(
            allowed_roots=(input_root,)
        )
    )

    result = execution.execute(
        operation="index",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "input_ref": input_ref,
            "preparation": {
                "schema": "ananta.vector_index_preparation.v1",
                "kind": "codecompass_documents",
                "embedding": {
                    "provider": "local_hash",
                    "model_version": "hash-v1",
                    "dimensions": 12,
                    "timeout_seconds": 20,
                    "external_calls_allowed": False,
                    "allowed_base_urls": [],
                },
                "embedding_text_profile": (
                    "codecompass-symbol-path-summary-v1"
                ),
            },
            "compatibility": {
                "dimensions": 12,
                "distance": "cosine",
                "provider": "local_hash",
                "model": "hash-v1",
                "profile": "codecompass-symbol-path-summary-v1",
                "encoding": "float32",
                "config_hash": "config-a",
                "schema_version": "codecompass_vector_index.v2",
                "manifest_hash": "manifest-a",
            },
        },
        idempotency_key="document-input-idempotency",
    )

    assert result["status"] == "completed"
    assert result["result"]["upserted"] == 1
    persisted = json.loads(
        (tmp_path / "index.json").read_text(encoding="utf-8")
    )
    assert len(persisted["entries"][0]["vector"]) == 12


def test_delete_all_scope_preserves_other_scope(tmp_path: Path) -> None:
    index_path = tmp_path / "index.json"
    execution = ConfiguredVectorIndexExecution()

    def execute_for(workspace: str, operation: str, payload: dict):
        return execution.execute(
            operation=operation,
            scope={
                "workspace_id": workspace,
                "repository_id": "repository-a",
                "profile_name": "default",
                "domain": "codecompass",
            },
            resolved_config=_resolved(index_path),
            payload=payload,
            idempotency_key=f"{workspace}-{operation}-request",
        )

    for workspace in ("workspace-a", "workspace-b"):
        result = execute_for(
            workspace,
            "index",
            {
                "points": [
                    {
                        "record_id": f"record-{workspace}",
                        "vector": [1.0, 0.0],
                        "payload": {"kind": "code"},
                        "source_hash": f"source-{workspace}",
                    }
                ]
            },
        )
        assert result["status"] == "completed"

    deleted = execute_for(
        "workspace-a",
        "delete",
        {"delete_all_scope": True},
    )
    persisted = json.loads(index_path.read_text(encoding="utf-8"))

    assert deleted["status"] == "completed"
    assert deleted["result"]["deleted"] == 1
    assert {
        entry["workspace_id"] for entry in persisted["entries"]
    } == {"workspace-b"}


def test_worker_rejects_string_delete_all_scope(tmp_path: Path) -> None:
    execution = ConfiguredVectorIndexExecution()
    result = execution.execute(
        operation="delete",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={"delete_all_scope": "false"},
        idempotency_key="delete-string-bool-key",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "vector_index_delete_all_scope_invalid"


@pytest.mark.parametrize("batch_size", [True, "128", 128.0, 0, 1001])
def test_worker_rejects_invalid_batch_sizes(
    tmp_path: Path,
    batch_size: object,
) -> None:
    execution = ConfiguredVectorIndexExecution()
    result = execution.execute(
        operation="index",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "batch_size": batch_size,
            "points": [
                {
                    "record_id": "record-a",
                    "vector": [1.0, 0.0],
                    "payload": {"kind": "code"},
                    "source_hash": "source-a",
                }
            ],
        },
        idempotency_key="invalid-batch-size",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "vector_batch_size_invalid"


def test_worker_propagates_batch_plan_to_qdrant_rebuild_and_refresh() -> None:
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )

    class Execution(ConfiguredVectorIndexExecution):
        def _create_store(self, config):
            return store

    execution = Execution()
    scope = {
        "workspace_id": "workspace-a",
        "repository_id": "repository-a",
        "profile_name": "default",
        "domain": "codecompass",
    }
    resolved = {
        "schema": "ananta.vector_store_resolved_config.v1",
        "config": {"provider": "qdrant", "qdrant": {}},
    }
    compatibility = {
        "dimensions": 2,
        "distance": "cosine",
        "provider": "test",
        "model": "v1",
        "profile": "default",
        "encoding": "float32",
        "config_hash": "cfg",
        "schema_version": "vector_store.v1",
        "manifest_hash": "manifest",
    }

    def point_payload(source_prefix: str) -> list[dict[str, object]]:
        return [
            {
                "record_id": f"record-{index}",
                "vector": [1.0, 0.0],
                "payload": {"kind": "code"},
                "source_hash": f"{source_prefix}-{index}",
            }
            for index in range(3)
        ]

    rebuilt = execution.execute(
        operation="rebuild",
        scope=scope,
        resolved_config=resolved,
        payload={
            "batch_size": 1,
            "compatibility": compatibility,
            "points": point_payload("source"),
        },
        idempotency_key="planned-rebuild-request",
    )
    rebuild_upserts = client.calls["upsert"]
    refreshed = execution.execute(
        operation="refresh",
        scope=scope,
        resolved_config=resolved,
        payload={
            "batch_size": 1,
            "compatibility": compatibility,
            "points": point_payload("changed"),
        },
        idempotency_key="planned-refresh-request",
    )

    assert rebuilt["status"] == "completed"
    assert rebuilt["result"]["diagnostics"]["batch_size"] == 1
    assert rebuild_upserts == 4  # one manifest plus three data batches
    assert refreshed["status"] == "completed"
    assert refreshed["result"]["diagnostics"]["batch_size"] == 1
    assert client.calls["upsert"] - rebuild_upserts == 3


def test_migration_payload_supports_dry_run_pause_and_bound_resume(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    source = json.dumps(
        {
            "state": {
                "schema": "codecompass_vector_index.v2",
                "embedding_provider": "test",
                "embedding_model_name": "v1",
                "embedding_dimensions": 2,
                "distance": "cosine",
                "embedding_provider_config_hash": "cfg",
                "embedding_text_profile": "default",
                "manifest_hash": "manifest",
                "vector_encoding_profile": {"mode": "off"},
            },
            "entries": [
                {
                    "record_id": "a",
                    "vector": [1.0, 0.0],
                    "kind": "code",
                },
                {
                    "record_id": "b",
                    "vector": [0.0, 1.0],
                    "kind": "code",
                },
            ],
        }
    ).encode("utf-8")
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )

    class Execution(ConfiguredVectorIndexExecution):
        def _create_store(self, config):
            return store

    execution = Execution(
        input_loader=BoundedVectorIndexInputLoader(
            allowed_roots=(input_root,)
        )
    )
    scope = {
        "workspace_id": "workspace-a",
        "repository_id": "repository-a",
        "profile_name": "default",
        "domain": "codecompass",
    }
    input_ref = _write_input_ref(input_root, source, scope=scope)
    resolved = {
        "schema": "ananta.vector_store_resolved_config.v1",
        "config": {
            "provider": "qdrant",
            "qdrant": {},
        },
    }
    base_payload = {
        "compatibility": {
            "dimensions": 2,
            "distance": "cosine",
            "provider": "test",
            "model": "v1",
            "profile": "default",
            "encoding": "float32",
            "config_hash": "cfg",
            "schema_version": "vector_store.v1",
            "manifest_hash": "manifest",
        },
        "batch_size": 1,
    }

    dry_run = execution.execute(
        operation="migrate",
        scope=scope,
        resolved_config=resolved,
        payload={
            **base_payload,
            "input_ref": input_ref,
            "migration": {"dry_run": True},
        },
        idempotency_key="migration-request-a",
    )
    assert dry_run["status"] == "completed", dry_run
    assert dry_run["reason_code"] == "migration_ready"
    assert dry_run["result"]["diagnostics"]["distance"] == "cosine"
    assert len(dry_run["result"]["diagnostics"]["scope_fingerprint"]) == 64
    assert client.collections == {}

    paused = execution.execute(
        operation="migrate",
        scope=scope,
        resolved_config=resolved,
        payload={
            **base_payload,
            "input_ref": input_ref,
            "migration": {
                "dry_run": False,
                "max_batches": 1,
            },
        },
        idempotency_key="migration-request-a",
    )
    checkpoint = paused["result"]["checkpoint"]
    resumed = execution.execute(
        operation="migrate",
        scope=scope,
        resolved_config=resolved,
        payload={
            **base_payload,
            "input_ref": input_ref,
            "migration": {
                "dry_run": False,
                "checkpoint": checkpoint,
            },
        },
        idempotency_key="migration-request-a",
    )

    assert paused["status"] == "failed"
    assert paused["reason_code"] == "migration_paused"
    assert len(checkpoint["scope_fingerprint"]) == 64
    assert len(checkpoint["idempotency_key_hash"]) == 64
    assert resumed["status"] == "completed"
    assert resumed["result"]["activated"] is True
