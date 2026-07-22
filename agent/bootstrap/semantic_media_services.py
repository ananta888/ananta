"""Composition root for Hub-owned semantic media programme services."""

from __future__ import annotations

import hashlib
import json
import os
import time

from flask import Flask

from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.semantic_media_audit_repository import (
    SqlSemanticMediaAuditRepository,
)
from agent.repositories.semantic_media_capability_grant_repository import (
    SqlSemanticMediaCapabilityGrantRepository,
)
from agent.repositories.sfu_broadcast_feature_flag_repository import (
    SqlSfuBroadcastFeatureFlagRepository,
)
from agent.repositories.sfu_broadcast_flag_projection_repository import (
    SqlSfuBroadcastFlagProjectionRepository,
)
from agent.repositories.sfu_broadcast_admission_operation_repository import (
    SqlSfuBroadcastAdmissionOperationRepository,
)
from agent.repositories.sfu_broadcast_background_job_repository import (
    SqlSfuBroadcastBackgroundJobRepository,
)
from agent.repositories.sfu_capacity_reservation_repository import (
    SqlSfuCapacityReservationRepository,
)
from agent.repositories.sfu_node_repository import SqlSfuNodeRepository
from agent.repositories.sfu_node_observation_cursor_repository import (
    SqlSfuNodeObservationCursorRepository,
)
from agent.repositories.sfu_runtime_identity_repository import SqlSfuRuntimeIdentityRepository
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
from agent.services.sfu_broadcast_feature_policy import SfuBroadcastFeaturePolicy
from agent.services.sfu_broadcast_flag_projection_service import (
    SfuBroadcastFlagProjectionService,
)
from agent.services.sfu_broadcast_runtime_control_port import (
    UnsupportedSfuRuntimeControlBoundary,
)
from agent.services.sfu_broadcast_admission_saga import (
    SfuBroadcastAdmissionFacade,
    SfuBroadcastAdmissionSaga,
    UnavailableSfuBroadcastAdmissionPlanResolver,
    UnavailableSfuBroadcastAdmissionPort,
)
from agent.services.sfu_broadcast_reconciler_scheduler import (
    CallableSfuBroadcastJob,
    SfuBroadcastReconcilerScheduler,
    load_sfu_broadcast_background_specs,
)
from agent.services.sfu_capacity_reservation_service import (
    SfuCapacityReservationPolicy,
    SfuCapacityReservationService,
)
from agent.services.sfu_node_health_evaluator import SfuNodeHealthEvaluator
from agent.services.sfu_receiver_quality_ingestion_service import (
    AdmissionBackedSfuReceiverQualityAuthority,
    SfuReceiverQualityIngestionService,
    build_sfu_receiver_quality_validator,
)
from agent.services.sfu_node_identity_service import SfuNodeIdentityService, SfuNodeTrustPolicy
from agent.services.sfu_node_observation_ingestion_service import (
    SfuNodeObservationIngestionService,
    SfuNodeObservationPolicy,
    build_sfu_node_observation_validator,
    collector_token_digest,
)


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
        configure_semantic_sfu_vendor_identities,
        get_semantic_sfu_admission_service,
    )
    from agent.services.semantic_sfu_group_key_service import (
        configure_semantic_sfu_group_key_audit,
        get_semantic_sfu_group_key_service,
    )
    from agent.repositories.sfu_vendor_identity_repository import SqlSfuVendorIdentityRepository
    from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope
    from agent.services.sfu_vendor_identity_service import SfuVendorIdentityService

    configure_semantic_sfu_admission_audit(recorder)
    sfu_hub_envelope = derive_sfu_hub_envelope(
        str(app.secret_key or ""), key_id="sfu-hub-v1"
    )
    vendor_identity_repository = SqlSfuVendorIdentityRepository()
    vendor_identity_service = SfuVendorIdentityService(
        vendor_identity_repository, sfu_hub_envelope, clock=time.time
    )
    configure_semantic_sfu_vendor_identities(vendor_identity_service)
    app.extensions["sfu_vendor_identity_repository"] = vendor_identity_repository
    app.extensions["sfu_vendor_identity_service"] = vendor_identity_service
    from agent.bootstrap.turn_accounting import initialize_turn_accounting

    initialize_turn_accounting(app)
    topology_policy = MediaTopologyPolicy()
    fanout = SemanticFanoutCoordinationService()
    configure_semantic_sfu_topology(topology_policy, fanout)
    quality_authority = AdmissionBackedSfuReceiverQualityAuthority(
        get_semantic_sfu_admission_service
    )
    app.extensions["sfu_receiver_quality_authority"] = quality_authority
    app.extensions["sfu_receiver_quality_ingestion_service"] = (
        SfuReceiverQualityIngestionService(
            authority=quality_authority,
            validator=build_sfu_receiver_quality_validator(clock=time.time),
            clock=time.time,
        )
    )
    app.extensions["semantic_media_topology_policy"] = topology_policy
    app.extensions["semantic_media_fanout_coordination"] = fanout
    configure_semantic_sfu_group_key_audit(recorder)
    app.extensions["semantic_sfu_group_key_service"] = get_semantic_sfu_group_key_service()
    app.extensions["semantic_media_debug_read_model"] = SemanticMediaDebugReadModel(repository)
    app.extensions["semantic_media_audit_lifecycle_service"] = SemanticMediaAuditLifecycleService(repository)
    flags = resolve_semantic_media_feature_flags(os.environ)
    app.extensions["semantic_media_feature_flags"] = flags
    broadcast_flag_repository = SqlSfuBroadcastFeatureFlagRepository()
    app.extensions["sfu_broadcast_feature_flag_repository"] = broadcast_flag_repository
    app.extensions["sfu_broadcast_feature_policy"] = SfuBroadcastFeaturePolicy(
        broadcast_flag_repository,
        static_source=flags,
    )
    runtime_control = app.extensions.get("sfu_broadcast_runtime_control_port")
    if runtime_control is None:
        runtime_control = UnsupportedSfuRuntimeControlBoundary()
        app.extensions["sfu_broadcast_runtime_control_port"] = runtime_control
    flag_projection_repository = SqlSfuBroadcastFlagProjectionRepository()
    flag_projection_service = SfuBroadcastFlagProjectionService(
        flag_projection_repository,
        runtime_control,
        clock=time.time,
    )
    app.extensions["sfu_broadcast_flag_projection_repository"] = flag_projection_repository
    app.extensions["sfu_broadcast_flag_projection_service"] = flag_projection_service
    sfu_identity_repository = SqlSfuRuntimeIdentityRepository()
    sfu_trust_policy = SfuNodeTrustPolicy.from_file(os.environ.get("ANANTA_SFU_NODE_TRUST_CONFIG"))
    app.extensions["sfu_runtime_identity_repository"] = sfu_identity_repository
    app.extensions["sfu_node_trust_policy"] = sfu_trust_policy
    app.extensions["sfu_node_identity_service"] = SfuNodeIdentityService(
        sfu_identity_repository,
        sfu_trust_policy,
    )
    sfu_node_cursor_key = hashlib.sha256(
        b"ananta:sfu-node-directory:cursor:v1\x00"
        + str(app.secret_key or "").encode("utf-8")
    ).digest()
    app.extensions["sfu_node_repository"] = SqlSfuNodeRepository(
        cursor_signing_key=sfu_node_cursor_key,
    )
    app.extensions["sfu_node_health_evaluator"] = SfuNodeHealthEvaluator(clock=time.time)
    capacity_repository = SqlSfuCapacityReservationRepository()
    capacity_policy = SfuCapacityReservationPolicy.fail_closed()
    app.extensions["sfu_capacity_reservation_repository"] = capacity_repository
    app.extensions["sfu_capacity_reservation_policy"] = capacity_policy
    app.extensions["sfu_capacity_reservation_service"] = (
        SfuCapacityReservationService(
            capacity_repository,
            capacity_policy,
            clock=time.time,
        )
    )
    admission_operation_repository = SqlSfuBroadcastAdmissionOperationRepository()
    unavailable_admission_port = UnavailableSfuBroadcastAdmissionPort()
    admission_saga = SfuBroadcastAdmissionSaga(
        admission_operation_repository,
        app.extensions.get("sfu_broadcast_admission_readiness_port", unavailable_admission_port),
        app.extensions.get("sfu_broadcast_admission_capacity_port", unavailable_admission_port),
        app.extensions.get("sfu_broadcast_admission_identity_port", unavailable_admission_port),
        app.extensions.get("sfu_broadcast_admission_route_port", unavailable_admission_port),
        clock=time.time,
    )
    legacy_admission = get_semantic_sfu_admission_service()
    app.extensions["sfu_broadcast_admission_operation_repository"] = admission_operation_repository
    app.extensions["sfu_broadcast_admission_saga"] = admission_saga
    app.extensions["semantic_sfu_admission_service"] = SfuBroadcastAdmissionFacade(
        legacy_admission,
        admission_saga,
        app.extensions.get(
            "sfu_broadcast_admission_plan_resolver",
            UnavailableSfuBroadcastAdmissionPlanResolver(),
        ),
    )
    background_repository = SqlSfuBroadcastBackgroundJobRepository()
    from agent.repositories.sfu_broadcast_repository import (
        SqlSfuAudienceSnapshotRetentionRepository,
    )
    from agent.services.sfu_audience_snapshot_retention_service import (
        SfuAudienceSnapshotRetentionService,
    )

    audience_retention_repository = SqlSfuAudienceSnapshotRetentionRepository()
    audience_retention_service = SfuAudienceSnapshotRetentionService(
        audience_retention_repository, clock=time.time
    )
    app.extensions["sfu_audience_retention_repository"] = audience_retention_repository
    app.extensions["sfu_audience_retention_job"] = audience_retention_service
    from agent.bootstrap.sfu_broadcast_services import (
        initialize_sfu_broadcast_hub_composition,
    )

    initialize_sfu_broadcast_hub_composition(app)
    from agent.bootstrap.sfu_broadcast_final_composition import (
        initialize_sfu_broadcast_final_composition,
    )

    initialize_sfu_broadcast_final_composition(app)
    background_config = os.environ.get(
        "ANANTA_SFU_BACKGROUND_CONFIG",
        "config/sfu_broadcast_background.default.json",
    )
    try:
        background_specs = load_sfu_broadcast_background_specs(background_config)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        background_specs = ()
        app.extensions["sfu_broadcast_background_status"] = {
            "ready": False,
            "reason_code": "sfu_background_config_invalid",
        }
    else:
        app.extensions["sfu_broadcast_background_status"] = {
            "ready": True,
            "reason_code": None,
        }
    background_jobs = {
        "flag_projection": CallableSfuBroadcastJob(
            lambda context: _run_flag_projection_job(flag_projection_service, context)
        ),
        "admission_recovery": CallableSfuBroadcastJob(
            lambda context: _run_admission_recovery_job(admission_saga, context)
        ),
        "fleet_reconciliation": CallableSfuBroadcastJob(
            lambda context: _run_capacity_reconciliation_job(capacity_repository, context)
        ),
    }
    from agent.bootstrap.sfu_broadcast_maintenance import (
        initialize_sfu_broadcast_maintenance_jobs,
    )

    initialize_sfu_broadcast_maintenance_jobs(app.extensions)
    background_jobs.pop("fleet_reconciliation", None)
    fleet_job = app.extensions.get("sfu_fleet_reconciliation_job")
    if fleet_job is not None and hasattr(fleet_job, "run"):
        background_jobs["fleet_reconciliation"] = fleet_job
    for name, extension_name in (
        ("route_reconciliation", "sfu_fanout_route_reconciler_job"),
        ("audience_retention", "sfu_audience_retention_job"),
        ("command_outbox_delivery", "sfu_broadcast_command_outbox_delivery_job"),
        ("destruction_pending", "sfu_member_digest_destruction_pending_job"),
        ("blind_index_reindex", "sfu_hub_blind_index_reindex_job"),
        ("ttl_purge", "sfu_broadcast_ttl_purge_job"),
        ("observation_collection", "sfu_observation_collector_job"),
    ):
        if name == "observation_collection" and (
            sfu_trust_policy.runtime_control_mode != "livekit_control_api"
            or not bool(
                app.extensions.get("sfu_broadcast_final_composition_status", {}).get(
                    "livekit_observation_ready"
                )
            )
        ):
            continue
        extension_job = app.extensions.get(extension_name)
        if extension_job is not None and hasattr(extension_job, "run"):
            background_jobs[name] = extension_job
    app.extensions["sfu_broadcast_background_job_repository"] = background_repository
    app.extensions["sfu_broadcast_reconciler_scheduler"] = SfuBroadcastReconcilerScheduler(
        background_repository,
        background_jobs,
        background_specs,
        clock=time.time,
    )
    observation_policy = SfuNodeObservationPolicy.from_environment(
        os.environ,
        runtime_control_mode=sfu_trust_policy.runtime_control_mode,
    )
    observation_cursor_repository = SqlSfuNodeObservationCursorRepository()
    app.extensions["sfu_node_observation_cursor_repository"] = observation_cursor_repository
    app.extensions["sfu_node_observation_policy"] = observation_policy
    app.extensions["sfu_node_observation_ingestion_service"] = (
        SfuNodeObservationIngestionService(
            cursor_repository=observation_cursor_repository,
            node_repository=app.extensions["sfu_node_repository"],
            identity_service=app.extensions["sfu_node_identity_service"],
            policy=observation_policy,
            validator=build_sfu_node_observation_validator(clock=time.time),
            clock=time.time,
        )
    )
    collector_token = str(os.environ.get("ANANTA_SFU_OBSERVATION_COLLECTOR_TOKEN") or "")
    app.extensions["sfu_node_observation_collector_token_digest"] = (
        collector_token_digest(collector_token) if collector_token else None
    )
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


def _run_flag_projection_job(service, context) -> str | None:
    context.require_lease()
    service.propagate_once()
    return context.resume_cursor


def _run_admission_recovery_job(service, context) -> str | None:
    context.require_lease()
    service.recover_open(limit=context.batch_size_max)
    return context.resume_cursor


def _run_capacity_reconciliation_job(repository, context) -> str | None:
    context.require_lease()
    repository.reconcile_expired(limit=context.batch_size_max, now=time.time())
    return context.resume_cursor


__all__ = ["initialize_semantic_media_services"]
