"""Deterministic Hub evidence support for the Spreadsheet Studio research pack."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent.repositories.organization_source_catalog_repository import (
    SourceCatalogPublishingAuthority,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.organization_source_catalog_binding_service import (
    OrganizationSourceCatalogBindingService,
    canonical_sha256,
)
from agent.services.organization_source_catalog_publisher_service import (
    OrganizationSourceCatalogPublisherService,
)
from agent.services.source_catalog_authority_service import SourceCatalogAuthorityService
from agent.services.source_catalog_service import SourceCatalogService

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-research"
PROJECT_ID = "project-ananta"
ORGANIZATION_ID = "spreadsheet-studio"
SUBJECT_ID = "hub-research"
SOURCE_TASK_ID = "source-catalog-task-spreadsheet-studio-v1"
CATEGORY_TASK_ID = "category-research-task-spreadsheet-studio-v1"
ASSIGNMENT_ID = "assignment-spreadsheet-studio-v1"
DISPATCH_LEASE_ID = "lease-spreadsheet-studio-v1"
WORKER_ID = "spreadsheet-research-worker"
SOURCE_SCOPE = f"organization:{ORGANIZATION_ID}"
ANANTA_COMMIT = "5765779d7c291439b13356e2dfa9aebb0ced421a"
ANANTA_TREE = "fbc8d7b896f78c7f2e4987c923ece3a2cab8f0e3"
REVISION_DIGEST = hashlib.sha256(ANANTA_COMMIT.encode("ascii")).hexdigest()


def stable_digest(value: Any) -> str:
    return canonical_sha256(value)


@dataclass(frozen=True, slots=True)
class ResearchSource:
    locator: str
    content_hash: str
    source_kind: str
    license: str
    captured_revision: str


SOURCES = (
    ResearchSource(
        "AGENTS.md",
        "deeeecc8493de4c460e9f78f85c2353c6fc0491726c79339cbf3d0d95940deb2",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "docs/planning-pipeline.md",
        "b743eaf2d68d2244288b1222960a19f428a035d258a09929b978023c0a2e909b",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "ananta_contracts/spreadsheet_studio.py",
        "90873e2b2ca4dad32ea43f0d15e7c3f975c8a27e46f19b78ad8c365c0e087e48",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "agent/services/spreadsheet_saga_service.py",
        "064bb306cedfac8e59e04748d7093863c16026722519262986ec3f05f94c6be5",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "agent/services/spreadsheet_learning_service.py",
        "e54b67939e28f9972b69f802f3e87e7c6773fc5fe2fb261d7d1a42178a102094",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "worker/spreadsheet/libreoffice_executor.py",
        "c77dad90967c0645f87b6fa38281476ee74552936aa466279bf21065ee0139dc",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "docker/compose-next/compose.spreadsheet-studio.yml",
        "913d21fb4628e8a034540d0b0d48726b916a36c5ef62c94c0883794f3e80d294",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "frontend-angular/src/app/features/spreadsheet-studio/spreadsheet-studio-page.component.ts",
        "262e5e46836a8f18e18864965c246218e7d83895008b0996df777e75ddd1ec31",
        "repo_file",
        "repository-license",
        ANANTA_COMMIT,
    ),
    ResearchSource(
        "https://ecma-international.org/publications-and-standards/standards/ecma-376/",
        "fc4f55531098bd209ab77b2fd39e0f640a131dac58f1f02c3e497bb6cde4b282",
        "specification",
        "ECMA publication terms",
        "captured-2026-09-01",
    ),
    ResearchSource(
        "https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part4-formula/OpenDocument-v1.3-os-part4-formula.html",
        "5cb47df4985c6215565b3ad5f69ead309ea7c7ed4e270c62a87c99a0f727d3c7",
        "specification",
        "OASIS document terms",
        "ODF-1.3",
    ),
    ResearchSource(
        "https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html",
        "11fc737038d80432d01e8fe60938ba800d787955b58431010526d2c25d95978f",
        "documentation",
        "MPL-2.0 documentation project",
        "captured-2026-09-01",
    ),
    ResearchSource(
        "https://docs.docker.com/engine/security/seccomp/",
        "68de2e1a7ab4b533d124d6d04b22d0ec67ec871780df2853dda77c6f41d954b2",
        "security_guidance",
        "Apache-2.0 documentation",
        "captured-2026-09-01",
    ),
    ResearchSource(
        "https://owasp.org/www-community/attacks/CSV_Injection",
        "319ab6b422ae6faf62410266e642394eac16b5e104a50244007eab270228914c",
        "security_guidance",
        "CC-BY-SA-4.0",
        "captured-2026-09-01",
    ),
    ResearchSource(
        "https://raw.githubusercontent.com/unslothai/unsloth/74661077e5a1796953f8d350b82d2c249aa8d04d/README.md",
        "23865f2f6028701660d5f5cddcd3a8e74d07581e0c75ebfaebf0e40c825ecc10",
        "upstream_repository",
        "Apache-2.0",
        "74661077e5a1796953f8d350b82d2c249aa8d04d",
    ),
)


def source_manifest_core() -> dict[str, Any]:
    records = []
    for ordinal, source in enumerate(SOURCES, start=1):
        records.append(
            {
                "source_id": OrganizationSourceCatalogPublisherService.allocated_source_id(ordinal),
                "locator": source.locator,
                "content_hash": source.content_hash,
                "source_kind": source.source_kind,
                "license": source.license,
                "captured_revision": source.captured_revision,
            }
        )
    return {
        "schema": "ananta.spreadsheet-studio-source-manifest.v1",
        "captured_on": "2026-09-01",
        "ananta_commit": ANANTA_COMMIT,
        "ananta_tree": ANANTA_TREE,
        "repository_revision": REVISION_DIGEST,
        "policy": {
            "identities_allocated_by": "OrganizationSourceCatalogPublisherService",
            "assignment_allowlist_required": True,
            "runtime_claims_require_run_evidence": True,
            "external_payloads_redistributed": False,
        },
        "sources": records,
    }


def build_authoritative_catalog() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = source_manifest_core()
    manifest_hash = stable_digest(manifest["sources"])
    authority = SourceCatalogPublishingAuthority(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        owner_id=SUBJECT_ID,
        connection_id="spreadsheet-research-sources",
        connector_type="registered_workspace",
        sensitivity="internal",
        source_revision_id="srev-" + REVISION_DIGEST[:24],
        revision_digest=REVISION_DIGEST,
        source_manifest_digest=manifest_hash,
        admission_receipt_id="admission-spreadsheet-research-v1",
        admission_digest=stable_digest({"manifest_hash": manifest_hash, "admitted": True}),
        knowledge_index_id="index-spreadsheet-research-v1",
        index_run_id="index-run-spreadsheet-research-v1",
        index_source_scope=SOURCE_SCOPE,
        index_manifest_digest=manifest_hash,
        policy_snapshot_digest=stable_digest({"policy": "local-research-v1"}),
        active_generation=1,
    )
    binding_service = OrganizationSourceCatalogBindingService()
    selected = []
    record_bindings = []
    for record in manifest["sources"]:
        record_binding = {
            "source_id": record["source_id"],
            "record_file": "index.jsonl",
            "record_id": record["locator"],
            "path": record["locator"],
            "line_start": None,
            "line_end": None,
            "content_hash": record["content_hash"],
        }
        provenance_digest = binding_service.source_provenance_digest(
            organization_id=ORGANIZATION_ID,
            authority=authority,
            source_id=record["source_id"],
            record_binding=record_binding,
        )
        selected.append(
            {
                "source_id": record["source_id"],
                "source_version": REVISION_DIGEST,
                "tenant_id": TENANT_ID,
                "scope": SOURCE_SCOPE,
                "provenance_digest": provenance_digest,
                "engine": "knowledge_index",
                "kind": "repo_file" if record["source_kind"] == "repo_file" else "documentation",
                "path": record["locator"],
                "record_id": record["locator"],
                "content_hash": record["content_hash"],
                "manifest_hash": manifest_hash,
                "sensitivity": "internal" if record["source_kind"] == "repo_file" else "public",
            }
        )
        record_bindings.append(record_binding)
    context_hash = stable_digest({"revision": REVISION_DIGEST, "records": record_bindings})
    catalog = SourceCatalogService().build_catalog(
        task_id=SOURCE_TASK_ID,
        retrieval_payload={
            "selected": selected,
            "retrieval_trace": {
                "trace_id": "catalog-trace-" + context_hash[:24],
                "context_hash": context_hash,
                "manifest_hash": manifest_hash,
                "tenant_id": TENANT_ID,
                "scope": SOURCE_SCOPE,
            },
        },
    )
    publication = binding_service.build(
        organization_id=ORGANIZATION_ID,
        authority=authority,
        query_digests=[stable_digest("spreadsheet studio research")],
        query_limit=len(SOURCES),
        record_bindings=record_bindings,
    )
    return catalog, publication


def persisted_catalog_projection(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": catalog["schema"],
        "source_catalog_id": catalog["catalog_id"],
        "source_catalog_hash": catalog["catalog_hash"],
        "catalog_state": catalog["catalog_state"],
        "retrieval_trace_id": catalog["retrieval_trace_id"],
        "retrieval_context_hash": catalog["retrieval_context_hash"],
        "retrieval_manifest_hash": catalog["retrieval_manifest_hash"],
        "source_count": len(catalog["sources"]),
        "rejected_count": len(catalog["rejected_candidates"]),
        "sources": list(catalog["sources"]),
    }


class _TaskRepository:
    def __init__(self, task: Mapping[str, Any]) -> None:
        self._task = dict(task)

    def get_by_id(self, task_id: str) -> Mapping[str, Any] | None:
        return self._task if task_id == self._task["id"] else None


def resolve_persisted_catalog(catalog: Mapping[str, Any]) -> dict[str, Any]:
    projection = persisted_catalog_projection(catalog)
    task = {
        "id": SOURCE_TASK_ID,
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "organization_id": ORGANIZATION_ID,
        "status": "completed",
        "task_kind": "organization_source_catalog",
        "history": [
            {
                "event_type": "task_ingested",
                "actor": SUBJECT_ID,
                "details": {"source": "organization_source_catalog"},
            }
        ],
        "verification_status": {"source_catalog": projection},
    }
    resolved = SourceCatalogAuthorityService(_TaskRepository(task)).resolve(
        principal=ChatSessionPrincipal.from_values(TENANT_ID, SUBJECT_ID),
        catalog_task_id=SOURCE_TASK_ID,
        catalog_id=str(catalog["catalog_id"]),
        catalog_hash=str(catalog["catalog_hash"]),
        repository_revision=REVISION_DIGEST,
        manifest_hash=str(catalog["retrieval_manifest_hash"]),
        source_allowlist_version=str(catalog["catalog_hash"]),
        source_scope=SOURCE_SCOPE,
        allowed_task_sources={"organization_source_catalog"},
        allowed_task_kinds={"organization_source_catalog"},
        expected_task_tenant_id=TENANT_ID,
        expected_task_project_id=PROJECT_ID,
        expected_task_organization_id=ORGANIZATION_ID,
        organization_access_authorized=True,
    )
    return resolved.as_dict()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ANANTA_COMMIT",
    "ANANTA_TREE",
    "ASSIGNMENT_ID",
    "CATEGORY_TASK_ID",
    "DISPATCH_LEASE_ID",
    "ORGANIZATION_ID",
    "PROJECT_ID",
    "REVISION_DIGEST",
    "ROOT",
    "SOURCE_SCOPE",
    "SOURCE_TASK_ID",
    "SUBJECT_ID",
    "TENANT_ID",
    "WORKER_ID",
    "build_authoritative_catalog",
    "canonical_json",
    "persisted_catalog_projection",
    "resolve_persisted_catalog",
    "source_manifest_core",
    "stable_digest",
]
