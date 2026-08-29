import logging
import os
import time

from flask import Flask

from agent import db_models as _  # noqa: F401 - register SQLModel tables before init_db
from agent.bootstrap.background import start_background_services
from agent.bootstrap.extensions import configure_cors, configure_swagger, load_extensions
from agent.bootstrap.request_hooks import configure_audit_logger, register_request_hooks
from agent.bootstrap.routes import register_alias_routes, register_blueprints
from agent.bootstrap.runtime_hints import log_runtime_hints
from agent.bootstrap.startup import run_startup_phase
from agent.bootstrap.voice_runtime_cleanup import recover_voice_runtime_cleanup
from agent.common.error_handler import register_error_handler
from agent.common.logging import setup_logging
from agent.common.signals import setup_signal_handlers
from agent.config import settings
from agent.database import init_db
from agent.metrics import APP_STARTUP_DURATION
from agent.services.app_runtime_service import build_base_app_config, initialize_runtime_state
from agent.services.deterministic_repair_handler import DeterministicRepairHandler
from agent.services.repository_registry import initialize_repository_registry
from agent.services.run_tests_handler import register_run_tests_handler
from agent.services.service_registry import initialize_core_services
from agent.services.task_handler_registry import register_task_handler
from agent.utils import read_json
from agent.utils import register_with_hub as _register_with_hub
from worker.core.template_propose_handler import TemplateProposeHandler

register_with_hub = _register_with_hub

_configure_audit_logger = configure_audit_logger
_configure_cors = configure_cors
_configure_swagger = configure_swagger
_load_extensions = load_extensions
_log_runtime_hints = log_runtime_hints
_register_alias_routes = register_alias_routes
_register_blueprints = register_blueprints
_register_request_hooks = register_request_hooks
_start_background_services = start_background_services


def _initialize_workflow_adapter_worker_runtime(app: Flask):
    if settings.role != "worker":
        app.extensions["workflow_adapter_worker_registration"] = {
            "capabilities": [],
            "runtime_targets": [],
            "reason_codes": ["workflow_adapter_worker_role_required"],
        }
        return None
    from worker.adapters.chain_runners import configure_text_generation
    from worker.runtime.native_graph.authorization import (
        HubBackedNativeAuthorizationVerifier,
        load_ed25519_native_authorization_verifier,
    )
    from worker.runtime.native_graph.composition import TaskScopedNativeWorkerExecutor
    from worker.runtime.native_worker_runtime_service import (
        get_native_worker_runtime_service,
    )
    from worker.runtime.provider_text_generation import (
        build_hub_budgeted_worker_text_generation,
    )
    from worker.runtime.workflow_adapter_runtime_composition import (
        initialize_workflow_adapter_worker_runtime,
    )
    from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient
    from worker.runtime.workspace_resolver import (
        ConfiguredWorkerWorkspaceResolver,
        WorkerWorkspaceResolutionError,
    )

    agent_config = dict(app.config.get("AGENT_CONFIG") or {})
    try:
        client = HttpWorkflowHubDecisionClient.from_environment()
    except ValueError:
        client = None
    configure_text_generation(
        build_hub_budgeted_worker_text_generation(
            client=client,
            provider_urls=dict(app.config.get("PROVIDER_URLS") or {}),
        )
        if client is not None
        else None
    )
    native_executor = None
    try:
        workspaces = ConfiguredWorkerWorkspaceResolver(agent_config)
    except WorkerWorkspaceResolutionError:
        pass
    else:
        native_executor = TaskScopedNativeWorkerExecutor(
            runtime=get_native_worker_runtime_service(),
            workspaces=workspaces,
        )
    try:
        native_authorization_verifier = (
            load_ed25519_native_authorization_verifier() or HubBackedNativeAuthorizationVerifier()
        )
    except ValueError:
        native_authorization_verifier = None

    return initialize_workflow_adapter_worker_runtime(
        app,
        client=client,
        native_executor=native_executor,
        native_authorization_verifier=native_authorization_verifier,
    )


def _should_skip_threads_for_reloader() -> bool:
    from agent.lifecycle import BackgroundServiceManager

    return BackgroundServiceManager(object())._should_skip_for_reloader()


def _register_deterministic_repair_handler(app: Flask) -> None:
    handler = DeterministicRepairHandler()
    register_task_handler(
        "admin_repair",
        handler,
        app=app,
        capabilities=["deterministic_repair", "shell_execute"],
        safety_flags={"requires_review": True, "requires_approval": True},
        verification_hooks=["step_verification", "final_verification"],
    )
    register_task_handler(
        "deterministic_repair",
        handler,
        app=app,
        capabilities=["deterministic_repair", "shell_execute"],
        safety_flags={"requires_review": True},
        verification_hooks=["step_verification"],
    )


def _register_template_propose_handler(app: Flask) -> None:
    handler = TemplateProposeHandler()
    for kind in ("new_software_project", "coding"):
        register_task_handler(
            kind,
            handler,
            app=app,
            capabilities=["template_propose", "shell_execute"],
            safety_flags={"requires_review": False},
        )


def _advertise_worker_capabilities(
    app: Flask,
    capabilities: list[str],
) -> None:
    """Add only capabilities backed by a successfully composed handler."""

    registration = dict(app.extensions.get("workflow_adapter_worker_registration") or {})
    registration["capabilities"] = sorted(
        {
            *(registration.get("capabilities") or []),
            *capabilities,
        }
    )
    app.extensions["workflow_adapter_worker_registration"] = registration


def _load_governed_knowledge_index_security_from_environment():
    """Compose the worker boundary without importing Hub implementations."""

    from agent.services.source_access_manifest_keyring import (
        load_source_access_manifest_keyring,
        resolve_knowledge_index_worker_id,
    )
    from worker.retrieval.governed_knowledge_index_worker_composition import (
        GovernedKnowledgeIndexWorkerSecurity,
    )

    keyring = load_source_access_manifest_keyring()
    security = GovernedKnowledgeIndexWorkerSecurity(
        worker_id=resolve_knowledge_index_worker_id(),
        verification_keys=keyring.verification_keys,
    )
    return security


def _build_governed_knowledge_index_handler_from_environment(*, security=None):
    """Compose the worker boundary without importing Hub implementations."""

    from worker.retrieval.governed_knowledge_index_worker_composition import (
        build_governed_knowledge_index_worker_handler,
    )

    return build_governed_knowledge_index_worker_handler(
        security=(security or _load_governed_knowledge_index_security_from_environment())
    )


def _register_worker_domain_handlers(app: Flask) -> None:
    """Compose worker-only execution adapters behind the shared task registry."""

    if settings.role != "worker":
        app.extensions["worker_domain_handler_registration"] = {
            "registered": [],
            "reason": "worker_role_required",
        }
        return

    from agent.adapters.vector_store_metrics_adapter import (
        PrometheusVectorStoreObserver,
    )
    from agent.cli_backends.sgpt import run_llm_cli_command
    from worker.core.model_provider import build_model_provider
    from worker.hrm_experiments.task_handler import (
        HrmExperimentWorkerConfigurationError,
        build_hrm_experiment_task_handler,
    )
    from worker.mail_task_execution import build_mail_task_handler
    from worker.planning import OrganizationCategoryResearchTaskHandler
    from worker.retrieval.sira.index_operation_handler import (
        LocalSiraIndexOperationRuntime,
        SiraIndexOperationTaskHandler,
    )
    from worker.retrieval.vector_index_execution import (
        ConfiguredVectorIndexExecution,
    )
    from worker.retrieval.vector_index_job_handler import (
        build_vector_index_task_handler,
    )
    from worker.retrieval.vector_index_task_verification import (
        UnavailableVectorIndexTaskVerifier,
        load_vector_index_task_verifier,
    )
    from worker.semantic_media.compute_task_handler import (
        SemanticComputeWorkerConfigurationError,
        build_semantic_compute_task_handler,
    )
    from worker.visual_process_assistant import (
        VisualProcessAssistantInferenceHandler,
        VisualProcessAssistantRetrievalHandler,
    )

    planning_research_capabilities = [
        "analysis",
        "planning",
        "research",
        "source_analysis",
        "structured_output",
    ]
    register_task_handler(
        "planning_research",
        OrganizationCategoryResearchTaskHandler(
            cli_runner=run_llm_cli_command,
            agent_config=dict(app.config.get("AGENT_CONFIG") or {}),
        ),
        app=app,
        capabilities=planning_research_capabilities,
        safety_flags={
            "requires_review": False,
            "worker_only": True,
            "hub_delegation_required": True,
            "worker_orchestration_forbidden": True,
            "peer_network_forbidden": True,
            "mutation_forbidden": True,
            "source_catalog_binding_required": True,
        },
        verification_hooks=[
            "todo_schema",
            "category_todo_quality_profile_v1",
            "source_catalog_binding",
            "assignment_reference_allowlist",
            "context_bundle_integrity",
        ],
    )
    _advertise_worker_capabilities(app, planning_research_capabilities)

    sira_snapshot_root = str(settings.codecompass_sira_snapshot_root or "").strip()
    sira_layer_root = str(settings.codecompass_sira_layer_root or "").strip()
    sira_registered = bool(sira_snapshot_root and sira_layer_root)
    if sira_registered:
        register_task_handler(
            "codecompass_sira_index_operation",
            SiraIndexOperationTaskHandler(
                LocalSiraIndexOperationRuntime(
                    snapshot_root=sira_snapshot_root,
                    layer_root=sira_layer_root,
                )
            ),
            app=app,
            capabilities=["retrieval", "index_write", "sira_index"],
            safety_flags={
                "requires_review": False,
                "worker_only": True,
                "hub_delegation_required": True,
                "worker_orchestration_forbidden": True,
                "client_filesystem_paths_forbidden": True,
            },
            verification_hooks=[
                "sira_index_operation_v1",
                "scope_binding",
                "idempotency_key",
                "atomic_activation",
            ],
        )
        _advertise_worker_capabilities(
            app,
            ["retrieval", "index_write", "sira_index"],
        )
    app.extensions["sira_index_worker_registration"] = {
        "ready": sira_registered,
        "reason_code": None if sira_registered else "sira_index_worker_paths_required",
    }

    knowledge_index_registered = False
    try:
        from agent.services.source_index_runtime_target import (
            load_source_index_runtime_target,
        )

        source_index_runtime_target = load_source_index_runtime_target()
        knowledge_index_security = _load_governed_knowledge_index_security_from_environment()
        knowledge_index_handler = _build_governed_knowledge_index_handler_from_environment(
            security=knowledge_index_security
        )
    except (RuntimeError, ValueError) as exc:
        reason_code = str(getattr(exc, "reason_code", "") or exc).strip()
        app.extensions["knowledge_index_worker_registration"] = {
            "ready": False,
            "reason_code": reason_code or "knowledge_index_worker_composition_invalid",
        }
    else:
        from agent.auth import resolve_configured_agent_token
        from agent.services.repository_registry import get_repository_registry
        from agent.services.source_access_manifest_signing import (
            WorkerSourceAccessManifestVerifier,
        )
        from worker.retrieval.knowledge_index_dispatch_receipt_repository import (
            SqlKnowledgeIndexWorkerDispatchReceiptRepository,
        )
        from worker.retrieval.knowledge_index_output_authorization import (
            KnowledgeIndexWorkerOutputCapabilityAuthorizer,
            LegacyKnowledgeIndexWorkerOutputAssignmentAuthorizer,
        )

        app.extensions["knowledge_index_worker_output_capability_authorizer"] = (
            KnowledgeIndexWorkerOutputCapabilityAuthorizer(
                task_repository=get_repository_registry().task_repo,
                receipt_ledger=(SqlKnowledgeIndexWorkerDispatchReceiptRepository()),
                manifest_verifier=WorkerSourceAccessManifestVerifier(dict(knowledge_index_security.verification_keys)),
                worker_id=knowledge_index_security.worker_id,
                worker_url=str(settings.agent_url or ""),
            )
        )
        app.extensions["legacy_knowledge_index_worker_output_assignment_authorizer"] = (
            LegacyKnowledgeIndexWorkerOutputAssignmentAuthorizer(
                task_repository=get_repository_registry().task_repo,
                worker_id=knowledge_index_security.worker_id,
                worker_url=str(settings.agent_url or ""),
                secret=str(resolve_configured_agent_token(app.config) or ""),
            )
        )
        register_task_handler(
            "codecompass_index_build",
            knowledge_index_handler,
            app=app,
            capabilities=["retrieval", "index_write"],
            safety_flags={
                "requires_review": False,
                "worker_only": True,
                "network_access": "hub_artifact_only",
                "signed_source_manifest_required": True,
            },
            verification_hooks=[
                "knowledge_index_job_result_schema",
                "idempotency_fingerprint",
                "artifact_first",
                "authenticated_worker_id",
                "source_access_manifest_signature",
            ],
        )
        _advertise_worker_capabilities(
            app,
            ["retrieval", "index_write"],
        )
        if source_index_runtime_target is not None:
            workflow_registration = dict(app.extensions.get("workflow_adapter_worker_registration") or {})
            runtime_targets = list(workflow_registration.get("runtime_targets") or [])
            runtime_target_id = source_index_runtime_target["runtime_target_id"]
            runtime_targets = [
                target
                for target in runtime_targets
                if not isinstance(target, dict) or target.get("runtime_target_id") != runtime_target_id
            ]
            runtime_targets.append(source_index_runtime_target)
            workflow_registration["runtime_targets"] = runtime_targets
            app.extensions["workflow_adapter_worker_registration"] = workflow_registration
        app.extensions["knowledge_index_worker_registration"] = {
            "ready": True,
            "reason_code": None,
        }
        knowledge_index_registered = True
    vector_registered = False
    try:
        vector_verifier = load_vector_index_task_verifier()
        if isinstance(
            vector_verifier,
            UnavailableVectorIndexTaskVerifier,
        ):
            raise ValueError("vector_index_task_verification_keyring_required")
        vector_handler = build_vector_index_task_handler(
            ConfiguredVectorIndexExecution(
                observer=PrometheusVectorStoreObserver(),
            ),
            task_verifier=vector_verifier,
        )
    except (RuntimeError, ValueError) as exc:
        reason = str(exc).strip()
        if not reason.startswith("vector_index_"):
            reason = "vector_index_worker_composition_invalid"
        app.extensions["vector_index_worker_registration"] = {
            "ready": False,
            "reason_code": reason,
        }
    else:
        vector_capabilities = [
            "retrieval",
            "index_write",
            "vector_index_operation",
        ]
        register_task_handler(
            "vector_index_operation",
            vector_handler,
            app=app,
            capabilities=vector_capabilities,
            safety_flags={
                "requires_review": False,
                "worker_only": True,
                "search_forbidden": True,
                "worker_orchestration_forbidden": True,
                "network_access": "configured_vector_store_only",
            },
            verification_hooks=[
                "vector_index_task_result_schema",
                "hub_attestation",
                "hub_dispatch_admission",
                "worker_audience",
                "dispatch_expiry",
                "durable_replay_guard",
                "dispatch_attempt_result_fence",
                "idempotency_key",
                "trusted_scope",
            ],
        )
        _advertise_worker_capabilities(
            app,
            vector_capabilities,
        )
        app.extensions["vector_index_worker_registration"] = {
            "ready": True,
            "reason_code": None,
        }
        vector_registered = True
    register_task_handler(
        "mail_operation",
        build_mail_task_handler(),
        app=app,
        capabilities=["mail_provider_execution"],
        safety_flags={
            "requires_review": False,
            "worker_only": True,
            "hub_delegation_required": True,
            "worker_orchestration_forbidden": True,
            "peer_network_forbidden": True,
            "content_in_task_payload_forbidden": True,
        },
        verification_hooks=[
            "mail_task_result_schema",
            "idempotency_key",
            "account_lease_fencing",
        ],
    )
    register_task_handler(
        "visual_process_assistant_retrieval",
        VisualProcessAssistantRetrievalHandler(),
        app=app,
        capabilities=["retrieval", "codecompass"],
        safety_flags={"requires_review": False, "worker_only": True, "read_only": True},
        verification_hooks=["source_ref_v2", "context_scanner", "evidence_release_gate"],
    )
    agent_config = dict(app.config.get("AGENT_CONFIG") or {})
    model_config = agent_config.get("visual_process_assistant_model")
    provider = build_model_provider(dict(model_config)) if isinstance(model_config, dict) else None
    register_task_handler(
        "visual_process_assistant_inference",
        VisualProcessAssistantInferenceHandler(provider),
        app=app,
        capabilities=["llm", "structured_output"],
        safety_flags={"requires_review": False, "worker_only": True, "mutation_forbidden": True},
        verification_hooks=["help_response_v1", "workflow_patch_v1", "source_ref_v2"],
    )
    registered = [
        "planning_research",
        *(["codecompass_sira_index_operation"] if sira_registered else []),
        *(["codecompass_index_build"] if knowledge_index_registered else []),
        *(["vector_index_operation"] if vector_registered else []),
        "mail_operation",
        "visual_process_assistant_retrieval",
        "visual_process_assistant_inference",
    ]
    from worker.training.unsloth_worker_runtime import (
        build_unsloth_worker_runtime,
    )

    unsloth_runtime = build_unsloth_worker_runtime()
    app.extensions["unsloth_worker_runtime"] = unsloth_runtime.health_snapshot()
    if unsloth_runtime.ready:
        workflow_registration = dict(app.extensions.get("workflow_adapter_worker_registration") or {})
        runtime_targets = list(workflow_registration.get("runtime_targets") or [])
        runtime_targets.append(
            {
                "runtime_target_id": ("unsloth-" + unsloth_runtime.profile.replace("_", "-")),
                "runtime_id": "unsloth",
                "adapter_id": unsloth_runtime.profile,
                "runtime_kind": "docker_container",
                "runtime_version": "1.0.0",
                "network_access": (unsloth_runtime.network_access),
            }
        )
        advertised = set(workflow_registration.get("capabilities") or [])
        for binding in unsloth_runtime.bindings:
            register_task_handler(
                binding.task_kind,
                binding.handler,
                app=app,
                capabilities=list(binding.capabilities),
                safety_flags=binding.safety_flags,
                verification_hooks=list(binding.verification_hooks),
            )
            advertised.update(binding.capabilities)
            registered.append(binding.task_kind)
        workflow_registration["capabilities"] = sorted(advertised)
        workflow_registration["runtime_targets"] = runtime_targets
        app.extensions["workflow_adapter_worker_registration"] = workflow_registration
    try:
        if os.environ.get("ANANTA_HRM_EXPERIMENT_WORKER_ENABLED", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise HrmExperimentWorkerConfigurationError("hrm_experiment_worker_disabled")
        hrm_experiment_handler = build_hrm_experiment_task_handler()
    except HrmExperimentWorkerConfigurationError as exc:
        app.extensions["hrm_experiment_worker_registration"] = {
            "ready": False,
            "reason_code": exc.reason_code,
        }
    else:
        hrm_projection = hrm_experiment_handler.capability()
        hrm_capabilities = [
            "hrm_experiment",
            "hrm_experiment.mock",
            "hrm_experiment.isolated_runner",
            *[f"hrm_experiment.profile.{profile_id}" for profile_id in hrm_projection.get("supported_profiles", [])],
        ]
        register_task_handler(
            "hrm_experiment",
            hrm_experiment_handler,
            app=app,
            capabilities=hrm_capabilities,
            safety_flags={
                "worker_only": True,
                "hub_delegation_required": True,
                "peer_network_forbidden": True,
                "child_task_creation_forbidden": True,
                "networkless_runner_required": True,
            },
            verification_hooks=[
                "hrm_run_request_v1",
                "hrm_run_result_v1",
                "authority_binding",
                "lease_fencing",
                "artifact_digest_verification",
            ],
        )
        registered.append("hrm_experiment")
        app.extensions["hrm_experiment_worker_registration"] = {
            "ready": True,
            "reason_code": None,
            "capability_digest": hrm_projection.get("capability_digest"),
            "supported_profiles": list(hrm_projection.get("supported_profiles") or []),
        }
        app.extensions["hrm_experiment_capability_heartbeat"] = hrm_experiment_handler.capability_heartbeat
        workflow_registration = dict(app.extensions.get("workflow_adapter_worker_registration") or {})
        workflow_registration["capabilities"] = sorted(
            {
                *(workflow_registration.get("capabilities") or []),
                *hrm_capabilities,
            }
        )
        app.extensions["workflow_adapter_worker_registration"] = workflow_registration
    try:
        if os.environ.get("ANANTA_SEMANTIC_COMPUTE_WORKER_ENABLED", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise SemanticComputeWorkerConfigurationError("semantic_compute_worker_disabled")
        semantic_compute_handler = build_semantic_compute_task_handler()
    except SemanticComputeWorkerConfigurationError as exc:
        app.extensions["semantic_compute_worker_registration"] = {
            "ready": False,
            "reason_code": exc.reason_code,
        }
    else:
        semantic_capabilities = [
            "semantic_compute",
            "semantic_compute.visual_extract",
            "semantic_compute.visual_validate",
            "semantic_compute.speech_features",
            "semantic_compute.speech_validate",
        ]
        register_task_handler(
            "semantic_compute",
            semantic_compute_handler,
            app=app,
            capabilities=semantic_capabilities,
            safety_flags={
                "worker_only": True,
                "hub_delegation_required": True,
                "peer_network_forbidden": True,
                "child_task_creation_forbidden": True,
            },
            verification_hooks=[
                "semantic_compute_task_v1",
                "semantic_compute_result_v1",
                "lease_fencing",
            ],
        )
        registered.append("semantic_compute")
        app.extensions["semantic_compute_worker_registration"] = {
            "ready": True,
            "reason_code": None,
        }
        workflow_registration = dict(app.extensions.get("workflow_adapter_worker_registration") or {})
        workflow_registration["capabilities"] = sorted(
            {
                *(workflow_registration.get("capabilities") or []),
                *semantic_capabilities,
            }
        )
        app.extensions["workflow_adapter_worker_registration"] = workflow_registration
    app.extensions["worker_domain_handler_registration"] = {"registered": registered}
    from agent.routes.worker_vector_index_readiness import (
        worker_vector_index_readiness_bp,
    )

    app.register_blueprint(worker_vector_index_readiness_bp)


def _check_token_rotation(app: Flask) -> None:
    """Backward-compatible token-rotation check used by legacy tests."""
    if str((app.config or {}).get("AGENT_TOKEN_FILE") or settings.agent_token_file or "").strip():
        return
    token_path = str((app.config or {}).get("TOKEN_PATH") or settings.token_path or "").strip()
    if not token_path or not os.path.exists(token_path):
        return
    try:
        token_data = read_json(token_path) or {}
        last_rotation = float(token_data.get("last_rotation") or 0)
        rotation_interval = int(settings.token_rotation_days or 7) * 86400
        if time.time() - last_rotation > rotation_interval:
            logging.info("Token-Rotations-Intervall erreicht. Starte Rotation...")
            with app.app_context():
                from agent.auth import rotate_token

                rotate_token()
    except Exception as exc:
        logging.error(f"Fehler bei der Prüfung der Token-Rotation: {exc}")


def _validate_workflow_credential_boundary(app: Flask) -> None:
    """Fail startup when a credential crosses a workflow trust boundary."""

    from agent.auth import resolve_configured_agent_token
    from agent.services.repository_registry import get_repository_registry
    from agent.services.workflow_worker_service_auth import (
        registered_worker_auth_required,
        runtime_service_keyring_configured,
        validate_workflow_credential_disjointness,
    )

    if not registered_worker_auth_required(app.config) and not runtime_service_keyring_configured(app.config):
        return
    with app.app_context():
        agents = get_repository_registry(app).agent_repo.get_all() or ()
        validate_workflow_credential_disjointness(
            user_session_secret=app.secret_key,
            hub_service_token=resolve_configured_agent_token(app.config),
            worker_service_tokens=(str(getattr(agent, "token", "") or "") for agent in agents),
            config=app.config,
        )


def create_app(agent: str = "default", *, testing: bool = False) -> Flask:
    """Erzeugt die Flask-App fuer den Agenten (API-Server)."""
    _start_perf = time.perf_counter()
    if not testing:
        run_startup_phase("logging", setup_logging, level=settings.log_level, json_format=settings.log_json)
        run_startup_phase("signals", setup_signal_handlers)
        run_startup_phase("runtime_hints", log_runtime_hints)
        run_startup_phase("audit_logger", configure_audit_logger)
        run_startup_phase("database", init_db)
        run_startup_phase("voice_runtime_cleanup_recovery", recover_voice_runtime_cleanup)

    app = run_startup_phase("flask_app", Flask, __name__)
    app.config["TESTING"] = testing  # Set Flask's testing flag
    # Required for Flask session-backed auth flows (e.g., OIDC state/nonce storage).
    app.secret_key = settings.secret_key
    run_startup_phase("request_hooks", register_request_hooks, app)
    run_startup_phase("error_handlers", register_error_handler, app)
    run_startup_phase("cors", configure_cors, app)
    app.config.update(run_startup_phase("base_config", build_base_app_config, agent))
    run_startup_phase("swagger", configure_swagger, app)
    run_startup_phase("blueprints", register_blueprints, app)
    run_startup_phase("alias_routes", register_alias_routes, app)
    run_startup_phase("runtime_state", initialize_runtime_state, app)
    run_startup_phase("extensions", load_extensions, app)
    run_startup_phase("repository_registry", initialize_repository_registry, app)
    run_startup_phase(
        "workflow_credential_boundary",
        _validate_workflow_credential_boundary,
        app,
    )
    run_startup_phase("core_services", initialize_core_services, app)
    from agent.bootstrap.scrum_continuous_improvement import initialize_scrum_continuous_improvement

    run_startup_phase(
        "scrum_continuous_improvement",
        initialize_scrum_continuous_improvement,
        app,
    )
    from agent.bootstrap.agent_safety import initialize_agent_safety

    run_startup_phase("agent_safety", initialize_agent_safety, app)
    from agent.bootstrap.codecompass_sira_rollout import (
        initialize_codecompass_sira_rollout,
    )

    run_startup_phase(
        "codecompass_sira_rollout",
        initialize_codecompass_sira_rollout,
        app,
    )
    from agent.bootstrap.knowledge_expert_rollout import initialize_knowledge_expert_rollout

    run_startup_phase(
        "knowledge_expert_rollout",
        initialize_knowledge_expert_rollout,
        app,
    )
    from agent.bootstrap.local_model_runtime import (
        initialize_local_model_runtime_services,
    )

    run_startup_phase(
        "local_model_runtime_services",
        initialize_local_model_runtime_services,
        app,
    )
    from agent.bootstrap.semantic_media_services import (
        initialize_semantic_media_services,
    )

    run_startup_phase(
        "semantic_media_services",
        initialize_semantic_media_services,
        app,
    )
    run_startup_phase(
        "workflow_adapter_worker_runtime",
        _initialize_workflow_adapter_worker_runtime,
        app,
    )
    run_startup_phase("worker_domain_handlers", _register_worker_domain_handlers, app)
    if not testing:  # Skip background services in testing mode
        run_startup_phase("background_services", start_background_services, app)

    run_startup_phase("deterministic_repair_handler", _register_deterministic_repair_handler, app)
    run_startup_phase("run_tests_handler", register_run_tests_handler, app)
    run_startup_phase("template_propose_handler", _register_template_propose_handler, app)

    if not testing:
        elapsed = time.perf_counter() - _start_perf
        APP_STARTUP_DURATION.set(elapsed)
        logging.info(f"Ananta Agent '{agent}' (role={settings.role}) started in {elapsed:.4f}s")

    return app


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "")


if __name__ == "__main__":
    import sys

    if "--version" in sys.argv:
        from agent.config import settings

        print(f"Ananta Agent v{settings.version}")
        sys.exit(0)

    app = create_app()
    _debug = _env_flag("FLASK_DEBUG")
    _reload = _env_flag("FLASK_RELOAD") or _debug
    app.run(host="0.0.0.0", port=settings.port, threaded=True, debug=_debug, use_reloader=_reload)
