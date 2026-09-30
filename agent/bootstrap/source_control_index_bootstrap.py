"""Governed knowledge-index composition of the Source Control Hub.

Loads the source-access manifest keyring, builds the index production
composition and the payload capability authorizers, and records a bounded
readiness result instead of failing Hub startup when the keyring is absent.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from agent.services.knowledge_index_payload_authorization import (
    KnowledgeIndexPayloadCapabilityAuthorizer,
    LegacyKnowledgeIndexPayloadAssignmentAuthorizer,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.source_access_manifest_keyring import (
    SourceAccessManifestKeyringError,
    load_source_access_manifest_keyring,
)
from agent.services.source_access_manifest_signing import (
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.source_control_index_production_wiring import (
    build_source_control_index_production_composition,
)


def compose_source_index_governance(
    app,
    *,
    db_engine: Engine,
    destination_catalog: object,
    workspace_catalog: object,
    workspace_source_connector: object,
    source_scanner: object,
    source_admission_budgets: SourceAdmissionBudgets,
) -> None:
    """Compose index governance once and publish it via app.extensions."""


    index_composition = app.extensions.get(
        "source_control_index_production_composition"
    )
    if index_composition is None:
        try:
            configured_signing_key = app.extensions.get(
                "source_access_signing_key"
            )
            if isinstance(configured_signing_key, SourceAccessSigningKey):
                source_access_signing_key = configured_signing_key
                source_access_verification_keys = {
                    configured_signing_key.key_id: (
                        configured_signing_key.secret
                    )
                }
            else:
                source_access_keyring = (
                    load_source_access_manifest_keyring()
                )
                source_access_signing_key = (
                    source_access_keyring.active_signing_key
                )
                source_access_verification_keys = (
                    source_access_keyring.verification_keys
                )
        except SourceAccessManifestKeyringError as exc:
            app.extensions["source_control_index_governance_readiness"] = {
                "ready": False,
                "reason_code": exc.reason_code,
            }
        else:
            app.extensions[
                "source_access_signing_key"
            ] = source_access_signing_key
            source_access_manifest_verifier = (
                WorkerSourceAccessManifestVerifier(
                    source_access_verification_keys
                )
            )
            index_composition = (
                build_source_control_index_production_composition(
                    app=app,
                    engine=db_engine,
                    destination_catalog=destination_catalog,
                    workspace_catalog=workspace_catalog,
                    workspace_connector=workspace_source_connector,
                    scanner=source_scanner,
                    budgets=source_admission_budgets,
                    signing_key=source_access_signing_key,
                    source_access_manifest_verifier=(
                        source_access_manifest_verifier
                    ),
                )
            )
            app.extensions[
                "source_control_index_production_composition"
            ] = index_composition
            app.extensions[
                "source_control_index_authority_planner"
            ] = index_composition.planner
            app.extensions[
                "source_control_governed_knowledge_index_job_service"
            ] = index_composition.job_service
            app.extensions[
                "knowledge_index_execution_binding_service"
            ] = index_composition.execution_binding_service
            app.extensions[
                "knowledge_index_payload_capability_authorizer"
            ] = KnowledgeIndexPayloadCapabilityAuthorizer(
                execution_binding_service=(
                    index_composition.execution_binding_service
                ),
                manifest_verifier=source_access_manifest_verifier,
                agent_repository=(
                    get_repository_registry().agent_repo
                ),
            )
            app.extensions[
                "legacy_knowledge_index_payload_assignment_authorizer"
            ] = LegacyKnowledgeIndexPayloadAssignmentAuthorizer(
                task_repository=get_repository_registry().task_repo,
            )
            app.extensions["source_control_index_governance_readiness"] = {
                "ready": True,
                "reason_code": None,
            }


__all__ = ["compose_source_index_governance"]
