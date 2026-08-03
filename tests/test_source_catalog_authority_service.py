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
from agent.services.source_catalog_service import (
    calculate_source_catalog_hash,
    calculate_source_catalog_id,
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
            "schema": "ananta.source_ref.v2",
            **provenance,
            "provenance_digest": digest,
        },
        **provenance,
        "provenance_digest": digest,
        "source_type": "repo_file",
        "path": "agent/runtime.py",
        "record_id": "record-a",
        "line_start": None,
        "line_end": None,
        "content_hash": "c" * 64,
        "manifest_hash": MANIFEST,
        "sensitivity": "internal",
        "allowed_for_llm_scope": True,
        "task_id": "catalog-task-a",
    }


def _task_payload(*, with_authority: bool = False) -> dict:
    sources = [_authorized_source()] if with_authority else [{}]
    task = {
        "id": "catalog-task-a",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
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
    if with_authority:
        _rehash_catalog(task)
    return task


def _rehash_catalog(task: dict) -> None:
    catalog = task["verification_status"]["source_catalog"]
    canonical = {
        "task_id": task["id"],
        "retrieval_trace_id": catalog["retrieval_trace_id"],
        "retrieval_context_hash": catalog["retrieval_context_hash"],
        "retrieval_manifest_hash": catalog["retrieval_manifest_hash"],
        "sources": catalog["sources"],
        "rejected_candidates": [],
    }
    catalog_hash = calculate_source_catalog_hash(canonical)
    catalog["source_catalog_hash"] = catalog_hash
    catalog["source_catalog_id"] = calculate_source_catalog_id(catalog_hash)


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
        expected_task_tenant_id="tenant-a",
        expected_task_project_id="project-a",
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
            lambda task: task.update(tenant_id="tenant-b"),
            "task_tenant_forbidden",
            False,
        ),
        (
            lambda task: task.update(project_id="project-b"),
            "task_project_forbidden",
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
    if needs_authority:
        _rehash_catalog(task)
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
        "expected_task_tenant_id": "tenant-a",
        "expected_task_project_id": "project-a",
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


def test_resolver_recomputes_catalog_hash_and_id() -> None:
    task = _task_payload(with_authority=True)
    catalog = task["verification_status"]["source_catalog"]
    catalog["sources"][0]["content_hash"] = "f" * 64
    service = SourceCatalogAuthorityService(
        SimpleNamespace(get_by_id=lambda _task_id: task)
    )

    with pytest.raises(
        SourceCatalogAuthorityError,
        match="hash_integrity_mismatch",
    ):
        _resolve(service, task)

    task = _task_payload(with_authority=True)
    task["verification_status"]["source_catalog"][
        "source_catalog_id"
    ] = "catalog-ffffffffffffffff"
    service = SourceCatalogAuthorityService(
        SimpleNamespace(get_by_id=lambda _task_id: task)
    )
    with pytest.raises(
        SourceCatalogAuthorityError,
        match="id_integrity_mismatch",
    ):
        _resolve(service, task)


def test_resolver_validates_complete_v2_payload() -> None:
    task = _task_payload(with_authority=True)
    catalog = task["verification_status"]["source_catalog"]
    catalog["sources"][0]["source_type"] = "repo_symbol"
    _rehash_catalog(task)
    service = SourceCatalogAuthorityService(
        SimpleNamespace(get_by_id=lambda _task_id: task)
    )

    with pytest.raises(
        SourceCatalogAuthorityError,
        match="payload_invalid",
    ):
        _resolve(service, task)


def test_organization_catalog_uses_scoped_authority_not_publisher_identity() -> None:
    task = _task_payload(with_authority=True)
    task["organization_id"] = "organization-a"
    source = task["verification_status"]["source_catalog"]["sources"][0]
    provenance = {
        "source_id": source["source_id"],
        "source_version": source["source_version"],
        "tenant_id": source["tenant_id"],
        "scope": "organization:organization-a",
    }
    provenance_digest = hashlib.sha256(
        json.dumps(
            provenance,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    source.update(
        {
            "scope": provenance["scope"],
            "provenance_digest": provenance_digest,
        }
    )
    source["source_ref"].update(
        {
            "scope": provenance["scope"],
            "provenance_digest": provenance_digest,
        }
    )
    _rehash_catalog(task)
    catalog = task["verification_status"]["source_catalog"]
    service = SourceCatalogAuthorityService(
        SimpleNamespace(get_by_id=lambda _task_id: task)
    )

    resolved = service.resolve(
        principal=ChatSessionPrincipal.from_values("tenant-a", "user-b"),
        catalog_task_id=task["id"],
        catalog_id=catalog["source_catalog_id"],
        catalog_hash=catalog["source_catalog_hash"],
        repository_revision=REVISION,
        manifest_hash=MANIFEST,
        source_allowlist_version=catalog["source_catalog_hash"],
        source_scope="organization:organization-a",
        allowed_task_sources={"visual_process"},
        allowed_task_kinds={"codecompass_fts_search"},
        expected_task_tenant_id="tenant-a",
        expected_task_project_id="project-a",
        expected_task_organization_id="organization-a",
        organization_access_authorized=True,
    )

    assert resolved.catalog_task_id == task["id"]
    assert resolved.source_refs[0].scope == "organization:organization-a"


def test_organization_catalog_requires_exact_scope_and_access_proof() -> None:
    task = _task_payload(with_authority=True)
    task["organization_id"] = "organization-a"
    catalog = task["verification_status"]["source_catalog"]
    service = SourceCatalogAuthorityService(
        SimpleNamespace(get_by_id=lambda _task_id: task)
    )
    common = {
        "principal": ChatSessionPrincipal.from_values("tenant-a", "user-b"),
        "catalog_task_id": task["id"],
        "catalog_id": catalog["source_catalog_id"],
        "catalog_hash": catalog["source_catalog_hash"],
        "repository_revision": REVISION,
        "manifest_hash": MANIFEST,
        "source_allowlist_version": catalog["source_catalog_hash"],
        "source_scope": "organization:organization-a",
        "allowed_task_sources": {"visual_process"},
        "allowed_task_kinds": {"codecompass_fts_search"},
        "expected_task_tenant_id": "tenant-a",
        "expected_task_project_id": "project-a",
    }

    with pytest.raises(
        SourceCatalogAuthorityError,
        match="organization_authority_required",
    ):
        service.resolve(
            **common,
            expected_task_organization_id="organization-a",
        )
    with pytest.raises(
        SourceCatalogAuthorityError,
        match="task_organization_forbidden",
    ):
        service.resolve(
            **common,
            expected_task_organization_id="organization-b",
            organization_access_authorized=True,
        )
