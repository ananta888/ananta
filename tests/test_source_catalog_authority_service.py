from __future__ import annotations

import copy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.source_catalog_authority_service import (
    SourceCatalogAuthorityError,
    SourceCatalogAuthorityService,
)

REVISION = "a" * 64
MANIFEST = "b" * 64


def _authorized_source() -> dict:
    source_id = os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID", "").strip()
    if not source_id:
        pytest.skip("authoritative_source_evidence_unavailable")
    provenance = {
        "source_id": source_id,
        "source_version": REVISION,
        "tenant_id": "tenant-a",
        "scope": "repository",
    }
    digest = hashlib.sha256(json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "source_ref": {
            **provenance,
            "provenance_digest": digest,
        },
        **provenance,
        "provenance_digest": digest,
        "source_type": "repo_file",
        "path": "agent/runtime.py",
        "record_id": "record-a",
        "content_hash": "c" * 64,
        "manifest_hash": MANIFEST,
        "sensitivity": "internal",
        "allowed_for_llm_scope": True,
        "task_id": "catalog-task-a",
    }


def _task_payload(*, with_authority: bool = False) -> dict:
    sources = [_authorized_source()] if with_authority else [{}]
    return {
        "id": "catalog-task-a",
        "status": "completed",
        "task_kind": "codecompass_fts_search",
        "history": [
            {
                "event_type": "task_ingested",
                "actor": "user-a",
                "details": {"source": "visual_process"},
            }
        ],
        "verification_status": {
            "source_catalog": {
                "schema": "source_catalog.v2",
                "source_catalog_id": "catalog-authority-test",
                "source_catalog_hash": "c" * 64,
                "catalog_state": "current",
                "source_count": len(sources),
                "rejected_count": 0,
                "retrieval_trace_id": "trace-a",
                "retrieval_context_hash": "d" * 64,
                "retrieval_manifest_hash": MANIFEST,
                "sources": sources,
            }
        },
    }


def _resolve(service: SourceCatalogAuthorityService, task: dict | None = None):
    payload = task or _task_payload()
    catalog = payload["verification_status"]["source_catalog"]
    return service.resolve(
        principal=ChatSessionPrincipal.from_values("tenant-a", "user-a"),
        catalog_task_id="catalog-task-a",
        catalog_id=catalog["source_catalog_id"],
        catalog_hash=catalog["source_catalog_hash"],
        repository_revision=REVISION,
        manifest_hash=MANIFEST,
        source_allowlist_version=catalog["source_catalog_hash"],
        source_scope="repository",
        allowed_task_sources={"visual_process"},
        allowed_task_kinds={"codecompass_fts_search"},
    )


def test_resolver_releases_only_hub_persisted_source_refs() -> None:
    task = _task_payload(with_authority=True)
    repository = SimpleNamespace(get_by_id=lambda task_id: task if task_id == task["id"] else None)

    resolved = _resolve(SourceCatalogAuthorityService(repository), task)

    assert resolved.repository_revision == REVISION
    assert resolved.manifest_hash == MANIFEST
    assert resolved.source_allowlist_version == resolved.catalog_hash
    assert [reference.source_id for reference in resolved.source_refs] == [
        task["verification_status"]["source_catalog"]["sources"][0]["source_id"]
    ]
    assert resolved.source_refs[0].tenant_id == "tenant-a"
    assert "content" not in resolved.as_dict()["source_refs"][0]


@pytest.mark.parametrize(
    ("mutation", "reason", "needs_authority"),
    [
        (lambda task: task.update(status="running"), "task_not_completed", False),
        (lambda task: task.update(task_kind="coding"), "task_kind_forbidden", False),
        (
            lambda task: task["history"][0].update(actor="other-user"),
            "owner_forbidden",
            False,
        ),
        (
            lambda task: task["history"][0]["details"].update(source="ui"),
            "task_source_forbidden",
            False,
        ),
        (
            lambda task: task["verification_status"]["source_catalog"].update(catalog_state="degraded"),
            "not_current",
            False,
        ),
        (
            lambda task: task["verification_status"]["source_catalog"].update(retrieval_manifest_hash="e" * 64),
            "manifest_mismatch",
            False,
        ),
        (
            lambda task: task["verification_status"]["source_catalog"]["sources"][0]["source_ref"].update(
                tenant_id="tenant-b"
            ),
            "tenant_forbidden",
            True,
        ),
        (
            lambda task: task["verification_status"]["source_catalog"]["sources"][0]["source_ref"].update(
                scope="other"
            ),
            "scope_forbidden",
            True,
        ),
        (
            lambda task: task["verification_status"]["source_catalog"]["sources"][0]["source_ref"].update(
                source_version="f" * 64
            ),
            "repository_revision_mismatch",
            True,
        ),
        (
            lambda task: task["verification_status"]["source_catalog"]["sources"][0].update(
                content="browser supplied content"
            ),
            "content_forbidden",
            True,
        ),
    ],
)
def test_resolver_rejects_unbound_catalog_state(mutation, reason, needs_authority) -> None:
    task = copy.deepcopy(_task_payload(with_authority=needs_authority))
    mutation(task)
    service = SourceCatalogAuthorityService(SimpleNamespace(get_by_id=lambda _task_id: task))

    with pytest.raises(SourceCatalogAuthorityError, match=reason):
        _resolve(service, task)


def test_resolver_rejects_catalog_reference_and_allowlist_mismatches() -> None:
    task = _task_payload()
    catalog = task["verification_status"]["source_catalog"]
    service = SourceCatalogAuthorityService(SimpleNamespace(get_by_id=lambda _task_id: task))
    common = {
        "principal": ChatSessionPrincipal.from_values("tenant-a", "user-a"),
        "catalog_task_id": task["id"],
        "repository_revision": REVISION,
        "manifest_hash": MANIFEST,
        "source_scope": "repository",
        "allowed_task_sources": {"visual_process"},
        "allowed_task_kinds": {"codecompass_fts_search"},
    }

    with pytest.raises(SourceCatalogAuthorityError, match="catalog_id_mismatch"):
        service.resolve(
            **common,
            catalog_id="catalog-forged",
            catalog_hash=catalog["source_catalog_hash"],
            source_allowlist_version=catalog["source_catalog_hash"],
        )
    with pytest.raises(SourceCatalogAuthorityError, match="catalog_hash_mismatch"):
        service.resolve(
            **common,
            catalog_id=catalog["source_catalog_id"],
            catalog_hash="e" * 64,
            source_allowlist_version="e" * 64,
        )
    with pytest.raises(SourceCatalogAuthorityError, match="allowlist_version_mismatch"):
        service.resolve(
            **common,
            catalog_id=catalog["source_catalog_id"],
            catalog_hash=catalog["source_catalog_hash"],
            source_allowlist_version="e" * 64,
        )
