"""Composition root for Hub-owned semantic media programme services."""

from __future__ import annotations

import hashlib
import os

from flask import Flask

from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.semantic_media_audit_repository import (
    SqlSemanticMediaAuditRepository,
)
from agent.repositories.semantic_media_capability_grant_repository import (
    SqlSemanticMediaCapabilityGrantRepository,
)
from agent.services.media_topology_policy import MediaTopologyPolicy
from agent.services.semantic_fanout_coordination_service import SemanticFanoutCoordinationService
from agent.services.semantic_media_audit_lifecycle_service import SemanticMediaAuditLifecycleService
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_debug_read_model import SemanticMediaDebugReadModel
from agent.services.semantic_media_feature_flags import (
    resolve_semantic_media_feature_flags,
)
from agent.services.semantic_media_permission_service import SemanticMediaPermissionService


def initialize_semantic_media_services(app: Flask) -> None:
    """Wire persistent adapters once at Hub startup.

    Routes consume these interfaces through ``app.extensions`` and cannot
    replace the read-only debug projection with a mutating control service.
    Feature flags remain fail-closed and are projected for operational
    diagnostics without making browser state authoritative.
    """

    app.config["SEMANTIC_COMPUTE_SECURITY_CONFIRMED"] = _environment_boolean(
        "ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED",
        default=False,
    )
    app.config["SEMANTIC_COMPUTE_FALLBACK_HEALTHY"] = _environment_boolean(
        "ANANTA_SEMANTIC_COMPUTE_FALLBACK_HEALTHY",
        default=True,
    )
    repository = SqlSemanticMediaAuditRepository()
    app.extensions["semantic_media_audit_repository"] = repository
    audit_service = SemanticMediaAuditService(repository)
    app.extensions["semantic_media_audit_service"] = audit_service
    recorder = SemanticMediaAuditRecorder(
        audit_service,
        secret=str(app.secret_key or "").encode("utf-8"),
    )
    app.extensions["semantic_media_audit_recorder"] = recorder
    capability_repository = SqlSemanticMediaCapabilityGrantRepository()
    capability_signing_key = hashlib.sha256(
        b"ananta:semantic-media-capability:v1\x00"
        + str(app.secret_key or "").encode("utf-8")
    ).digest()
    app.extensions["semantic_media_capability_grant_repository"] = capability_repository
    app.extensions["semantic_media_permission_service"] = SemanticMediaPermissionService(
        capability_signing_key,
        repository=capability_repository,
        audit=recorder,
    )
    from agent.repositories.ml_intern_training import configure_ml_intern_training_audit
    from agent.services.ml_intern_speech_adapter_registry import (
        configure_ml_intern_speech_adapter_audit,
    )

    configure_ml_intern_training_audit(recorder)
    configure_ml_intern_speech_adapter_audit(recorder)
    outbox = SqlSemanticMediaAuditOutbox()
    app.extensions["semantic_media_audit_outbox"] = outbox
    from agent.repositories.semantic_lease_repository import get_semantic_lease_repository

    lease_repository = get_semantic_lease_repository(audit=recorder)
    app.extensions["semantic_lease_repository"] = lease_repository
    from agent.services.semantic_relay_composition import configure_semantic_relay_audit

    configure_semantic_relay_audit(recorder)
    from agent.services.speech_evidence_store_service import configure_speech_evidence_store_audit

    configure_speech_evidence_store_audit(recorder)
    from agent.services.speech_evidence_revocation_service import SpeechEvidenceRevocationService

    app.extensions["speech_evidence_revocation_service"] = SpeechEvidenceRevocationService(audit=recorder)
    from agent.services.semantic_sfu_admission_service import (
        configure_semantic_sfu_admission_audit,
        configure_semantic_sfu_topology,
    )
    from agent.services.semantic_sfu_group_key_service import configure_semantic_sfu_group_key_audit

    configure_semantic_sfu_admission_audit(recorder)
    topology_policy = MediaTopologyPolicy()
    fanout = SemanticFanoutCoordinationService()
    configure_semantic_sfu_topology(topology_policy, fanout)
    app.extensions["semantic_media_topology_policy"] = topology_policy
    app.extensions["semantic_media_fanout_coordination"] = fanout
    configure_semantic_sfu_group_key_audit(recorder)
    app.extensions["semantic_media_debug_read_model"] = SemanticMediaDebugReadModel(repository)
    app.extensions["semantic_media_audit_lifecycle_service"] = SemanticMediaAuditLifecycleService(repository)
    flags = resolve_semantic_media_feature_flags(os.environ)
    app.extensions["semantic_media_feature_flags"] = flags
    if flags.get("peer_evidence_sync", False):
        from agent.services.speech_evidence_sync_composition import (
            build_speech_evidence_sync_composition,
        )

        try:
            sync_composition = build_speech_evidence_sync_composition(audit=recorder)
        except Exception:
            app.logger.exception("speech evidence sync composition is unavailable")
            app.extensions["speech_evidence_sync_composition_status"] = {
                "ready": False,
                "reason_code": "speech_evidence_sync_unavailable",
            }
        else:
            app.extensions["speech_evidence_sync_composition"] = sync_composition
            app.extensions["speech_evidence_sync_service"] = sync_composition.service
            app.extensions["speech_evidence_sync_composition_status"] = {
                "ready": True,
                "reason_code": None,
            }
            from agent.services.speech_evidence_peer_curation_composition import (
                build_speech_peer_evidence_curation_service,
            )

            try:
                peer_curation = build_speech_peer_evidence_curation_service(
                    sync_composition.service,
                    audit=recorder,
                )
            except Exception:
                app.logger.exception("peer speech-evidence curation composition is unavailable")
                app.extensions["speech_peer_evidence_curation_status"] = {
                    "ready": False,
                    "reason_code": "speech_peer_curation_unavailable",
                }
            else:
                app.extensions["speech_peer_evidence_curation_service"] = peer_curation
                app.extensions["speech_peer_evidence_curation_status"] = {
                    "ready": True,
                    "reason_code": None,
                }
    else:
        app.extensions["speech_evidence_sync_composition_status"] = {
            "ready": False,
            "reason_code": "semantic_feature_disabled",
        }
        app.extensions["speech_peer_evidence_curation_status"] = {
            "ready": False,
            "reason_code": "semantic_feature_disabled",
        }
    if flags.get("speech_adapter_routing", False):
        from agent.services.ml_intern_speech_adapter_export import (
            SpeechAdapterExportConfigurationError,
            build_speech_adapter_export_service,
        )

        try:
            export_service = build_speech_adapter_export_service(os.environ, audit=recorder)
        except SpeechAdapterExportConfigurationError as exc:
            app.extensions.pop("speech_adapter_export_port", None)
            app.extensions["speech_adapter_export_status"] = {
                "ready": False,
                "reason_code": exc.reason_code,
            }
        else:
            app.extensions["speech_adapter_export_port"] = export_service
            app.extensions["speech_adapter_export_status"] = {
                "ready": True,
                "reason_code": None,
            }
    else:
        app.extensions.pop("speech_adapter_export_port", None)
        app.extensions["speech_adapter_export_status"] = {
            "ready": False,
            "reason_code": "semantic_feature_disabled",
        }
    if flags.get("speech_adaptation_training", False):
        from agent.services.speech_adaptation_production_composition import (
            HubSpeechAdaptationWorkerControl,
            SpeechAdaptationProductionConfigurationError,
            build_speech_adaptation_composition,
        )

        try:
            composition = build_speech_adaptation_composition(os.environ, audit=recorder)
        except SpeechAdaptationProductionConfigurationError as exc:
            app.extensions["speech_adaptation_composition_status"] = {
                "ready": False,
                "reason_code": exc.reason_code,
            }
        else:
            app.extensions["speech_adaptation_composition"] = composition
            app.extensions["speech_adaptation_job_service"] = composition.service
            app.extensions["speech_adapter_registration_admission"] = composition.adapter_registration
            app.extensions["speech_adaptation_worker_control"] = HubSpeechAdaptationWorkerControl(composition)
            app.extensions["speech_adaptation_composition_status"] = {
                "ready": True,
                "reason_code": None,
            }
            from agent.services.speech_reconciliation_training_delegate import (
                build_speech_reconciliation_training_admission,
            )

            try:
                training_admission = build_speech_reconciliation_training_admission(
                    composition.service,
                    os.environ,
                )
            except ValueError as exc:
                app.extensions["speech_reconciliation_training_admission_status"] = {
                    "ready": False,
                    "reason_code": str(exc),
                }
            else:
                app.extensions["speech_reconciliation_training_admission"] = training_admission
                app.extensions["speech_reconciliation_training_admission_status"] = {
                    "ready": True,
                    "reason_code": None,
                }


def _environment_boolean(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["initialize_semantic_media_services"]
