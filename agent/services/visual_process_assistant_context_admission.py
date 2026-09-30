"""Admission of an editor context snapshot for the Visual Process Assistant.

Validates the caller's draft against the persisted definition, resolves the
Hub source-catalog authority for evidence references and builds the
immutable :class:`EditorContextEnvelope`.  Clients can never supply source
references themselves; only Hub-authorized catalog identities are cited.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.source_catalog_authority_service import (
    SourceCatalogAuthorityError,
    SourceCatalogAuthorityService,
)
from agent.services.visual_process_assistant_errors import VisualProcessAssistantError
from agent.services.visual_process_assistant_validation import (
    required_text as _required_text,
)
from agent.services.visual_process_context_service import (
    PROMPT_VERSION,
    VisualProcessContextService,
)
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessGraph
from ananta_contracts.visual_process_assistant import (
    EditorContextEnvelope,
    EvidenceRef,
    TrustLevel,
    VerificationStatus,
)

ASSISTANT_CATALOG_TASK_SOURCES = frozenset({"api", "visual_process", "visual_process_assistant"})
ASSISTANT_CATALOG_TASK_KINDS = frozenset(
    {
        "codecompass_fts_search",
        "codecompass_graph_expand",
        "codecompass_vector_search",
        "rag_retrieve",
        "research_limited",
        "review",
        "summarize",
    }
)


class VisualProcessAssistantContextAdmission:
    """Build one Hub-authorized editor context envelope."""

    def __init__(
        self,
        *,
        context_service: VisualProcessContextService,
        source_authority: SourceCatalogAuthorityService,
    ) -> None:
        self._contexts = context_service
        self._source_authority = source_authority

    def admit(
        self,
        *,
        principal: ChatSessionPrincipal,
        graph: VisualProcessGraph,
        body: Mapping[str, Any],
    ) -> EditorContextEnvelope:
        draft = body.get("draft_graph")
        draft_graph = VisualProcessGraph.model_validate(draft) if isinstance(draft, Mapping) else graph
        if draft_graph.id != graph.id:
            raise VisualProcessAssistantError("assistant_draft_graph_mismatch", status_code=409)
        if draft_graph.definition_revision != graph.definition_revision:
            raise VisualProcessAssistantError(
                "assistant_context_definition_stale",
                status_code=409,
                details={
                    "expected_revision": graph.definition_revision,
                    "actual_revision": draft_graph.definition_revision,
                },
            )
        supplied_base = str(draft_graph.base_graph_hash or body.get("base_graph_hash") or "")
        if supplied_base and supplied_base.removeprefix("sha256:") != graph.base_graph_hash.removeprefix("sha256:"):
            raise VisualProcessAssistantError("assistant_context_definition_stale", status_code=409)
        VisualProcessDefinitionService.validate_writable_definition(draft_graph)

        repository_revision = _required_text(body, "repository_revision", max_length=256)
        manifest_hash = _required_text(body, "codecompass_manifest_hash", max_length=256)
        allowlist_version = _required_text(body, "source_allowlist_version", max_length=256)
        source_scope = _required_text(body, "source_scope", max_length=256)
        if "source_refs" in body:
            raise VisualProcessAssistantError(
                "assistant_client_source_refs_forbidden",
                status_code=403,
            )
        catalog_fields = {
            "catalog_task_id": str(body.get("catalog_task_id") or "").strip(),
            "catalog_id": str(body.get("catalog_id") or "").strip(),
            "catalog_hash": str(body.get("catalog_hash") or "").strip(),
        }
        if any(catalog_fields.values()) and not all(catalog_fields.values()):
            raise VisualProcessAssistantError(
                "assistant_source_catalog_reference_incomplete",
                status_code=422,
            )
        source_refs: list[EvidenceRef] = []
        context_extensions: dict[str, Any] = {"ananta.source_scope": source_scope}
        if all(catalog_fields.values()):
            try:
                resolved_catalog = self._source_authority.resolve(
                    principal=principal,
                    repository_revision=repository_revision,
                    manifest_hash=manifest_hash,
                    source_allowlist_version=allowlist_version,
                    source_scope=source_scope,
                    allowed_task_sources=ASSISTANT_CATALOG_TASK_SOURCES,
                    allowed_task_kinds=ASSISTANT_CATALOG_TASK_KINDS,
                    expected_task_tenant_id=principal.tenant_id,
                    **catalog_fields,
                )
            except SourceCatalogAuthorityError as exc:
                status_code = 404 if exc.reason_code.endswith("not_found") else 403
                raise VisualProcessAssistantError(
                    f"assistant_{exc.reason_code}",
                    status_code=status_code,
                ) from exc
            source_refs = [
                EvidenceRef(
                    # Claims cite the exact Hub-authorized identity.  The
                    # assistant must never mint a parallel evidence id.
                    evidence_id=reference.source_id,
                    source_id=reference.source_id,
                    source_version=reference.source_version,
                    tenant_id=reference.tenant_id,
                    scope=reference.scope,
                    provenance_digest=reference.provenance_digest,
                    trust_level=TrustLevel.declared,
                    verification_status=VerificationStatus.verified,
                )
                for reference in resolved_catalog.source_refs
            ]
            context_extensions["ananta.source_catalog"] = {
                "catalog_task_id": resolved_catalog.catalog_task_id,
                "catalog_id": resolved_catalog.catalog_id,
                "catalog_hash": resolved_catalog.catalog_hash,
            }
        editor_mode = str(body.get("editor_mode") or "editor").strip().lower()
        allowed_mutations = (
            []
            if editor_mode == "read_only"
            else [
                "add_step",
                "remove_step",
                "update_step_field",
                "add_edge",
                "remove_edge",
                "update_edge_condition",
            ]
        )
        envelope = self._contexts.build_context(
            graph=graph,
            draft_graph=draft_graph,
            location=dict(body.get("location") or {}),
            editor_mode=editor_mode,
            locale=str(body.get("locale") or "de"),
            repository_revision=repository_revision,
            codecompass_manifest_hash=manifest_hash,
            source_allowlist_version=allowlist_version,
            prompt_version=PROMPT_VERSION,
            runtime_overlay=(
                dict(body.get("runtime_overlay") or {})
                if isinstance(body.get("runtime_overlay"), Mapping)
                else None
            ),
            validation_issues=[
                dict(item) for item in list(body.get("validation_issues") or []) if isinstance(item, Mapping)
            ],
            evidence_refs=source_refs,
            allowed_mutations=allowed_mutations,
            extensions=context_extensions,
        )
        return envelope


__all__ = [
    "ASSISTANT_CATALOG_TASK_KINDS",
    "ASSISTANT_CATALOG_TASK_SOURCES",
    "VisualProcessAssistantContextAdmission",
]
