"""Least-privilege identities for Worker-to-Hub workflow requests."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)

WORKER_REGISTRATION_KEYRING_SCHEMA = "ananta.workflow-worker-registration-keyring.v1"
STRICT_WORKER_REGISTRATION_PROVENANCE = "strict_registration_keyring_v1"
WORKER_ID_HEADER = "X-Ananta-Worker-ID"
WORKER_URL_HEADER = "X-Ananta-Worker-URL"
RUNTIME_SERVICE_ID_HEADER = "X-Ananta-Service-ID"
RUNTIME_SERVICE_KEYRING_SCHEMA = "ananta.workflow-runtime-service-keyring.v1"

WORKFLOW_WORKER_COMMAND_SCOPE = "workflow.worker.commands"
WORKFLOW_LANGGRAPH_CHECKPOINT_SCOPE = "workflow.langgraph.checkpoints"
WORKFLOW_TEMPORAL_TASK_SCOPE = "workflow.temporal.tasks"
KNOWLEDGE_INDEX_PAYLOAD_SCOPE = "knowledge.index.payloads"
KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCOPE = (
    "knowledge.index.task_snapshot.read"
)
SEMANTIC_COMPUTE_WORKER_SCOPE = "semantic.compute.execute"
SPEECH_EVIDENCE_CURATION_WORKER_SCOPE = "speech.evidence.curate"
RECOVERY_TASK_DISPATCH_SCOPE = "task.recovery.dispatch"
RECOVERY_TASK_MANIFEST_SCOPE = "task.recovery.manifest.read"
RECOVERY_ARTIFACT_INGRESS_SCOPE = "task.recovery.artifacts.publish"
VECTOR_INDEX_DISPATCH_ADMISSION_SCOPE = (
    "task.vector_index.dispatch.admit"
)

_STRICT_ENV = "ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"
_KEYRING_FILE_ENV = "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE"
_RUNTIME_SERVICE_KEYRING_FILE_ENV = "ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE"
_MAX_KEYRING_BYTES = 262_144
_MAX_IDENTIFIER_BYTES = 256
_MIN_TOKEN_BYTES = 32
_MAX_TOKEN_BYTES = 16_384
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SCOPE_CAPABILITIES = {
    WORKFLOW_WORKER_COMMAND_SCOPE: frozenset({"workflow.adapter.native", "workflow.adapter.langgraph"}),
    WORKFLOW_LANGGRAPH_CHECKPOINT_SCOPE: frozenset({"workflow.adapter.langgraph"}),
    WORKFLOW_TEMPORAL_TASK_SCOPE: frozenset({"workflow.adapter.temporal", "workflow.runtime.temporal"}),
    KNOWLEDGE_INDEX_PAYLOAD_SCOPE: frozenset({"retrieval", "index_write"}),
    KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCOPE: frozenset(
        {"retrieval", "index_write"}
    ),
    SEMANTIC_COMPUTE_WORKER_SCOPE: frozenset(
        {
            "semantic_compute",
            "semantic_compute.visual_extract",
            "semantic_compute.visual_validate",
            "semantic_compute.speech_features",
            "semantic_compute.speech_validate",
        }
    ),
    SPEECH_EVIDENCE_CURATION_WORKER_SCOPE: frozenset({"speech_evidence_curation"}),
    # Recovery dispatch has request-bound capabilities.  Authentication proves
    # strict registry identity here; the recovery gate then requires every
    # capability declared by the concrete Hub task.
    RECOVERY_TASK_DISPATCH_SCOPE: frozenset(),
    # Recovery manifests are additionally bound to the authoritative task
    # assignment by the Hub endpoint.  The scope itself grants no generic
    # task-read capability.
    RECOVERY_TASK_MANIFEST_SCOPE: frozenset(),
    # Artifact publication is additionally fenced by the exact Recovery
    # dispatch lease and authoritative task assignment.  This scope grants no
    # generic Artifact create/read capability.
    RECOVERY_ARTIFACT_INGRESS_SCOPE: frozenset(),
    VECTOR_INDEX_DISPATCH_ADMISSION_SCOPE: frozenset(
        {"vector_index_operation"}
    ),
}
_RUNTIME_SERVICE_SCOPES = frozenset({WORKFLOW_TEMPORAL_TASK_SCOPE})


class WorkflowWorkerAuthConfigurationError(RuntimeError):
    """Raised when strict Worker authentication cannot be trusted."""


class WorkflowWorkerAuthDenied(RuntimeError):
    """Bounded authentication/registration denial with a stable reason code."""

    def __init__(self, reason_code: str, *, status_code: int = 401) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkerRegistrationCredential:
    worker_id: str
    worker_url: str
    registration_token: str
    service_token_sha256: str
    session_signing_key_sha256: str
    allowed_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class RegisteredWorkflowWorkerIdentity:
    worker_id: str
    worker_url: str
    capabilities: tuple[str, ...]
    auth_mode: str = "registered_worker_service_token"

    def auth_payload(self, *, scope: str) -> dict[str, Any]:
        return {
            "auth_mode": self.auth_mode,
            "token_use": "workflow_worker_service",
            "worker_id": self.worker_id,
            "worker_url": self.worker_url,
            "service_scope": str(scope),
        }


@dataclass(frozen=True)
class RuntimeServiceCredential:
    service_id: str
    token: str
    scopes: tuple[str, ...]

    def auth_payload(self, *, scope: str) -> dict[str, Any]:
        return {
            "auth_mode": "preconfigured_runtime_service_token",
            "token_use": "workflow_runtime_service",
            "service_id": self.service_id,
            "service_scope": str(scope),
        }


def registered_worker_auth_required(
    config: Mapping[str, Any] | None = None,
) -> bool:
    source = config or {}
    raw = source.get(_STRICT_ENV)
    if raw is None:
        raw = os.environ.get(_STRICT_ENV, "")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUE_VALUES


def load_worker_registration_keyring(
    config: Mapping[str, Any] | None = None,
) -> dict[str, WorkerRegistrationCredential]:
    source = config or {}
    path = str(source.get(_KEYRING_FILE_ENV) or os.environ.get(_KEYRING_FILE_ENV) or "").strip()
    if not path:
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_required")
    try:
        raw = read_file_managed_bytes(
            path,
            description="workflow Worker registration keyring file",
            max_bytes=_MAX_KEYRING_BYTES,
        )
        decoded = json.loads(raw.decode("utf-8"))
    except (FileCredentialConfigurationError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid") from exc
    if not isinstance(decoded, Mapping) or decoded.get("schema") != WORKER_REGISTRATION_KEYRING_SCHEMA:
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid")
    raw_workers = decoded.get("workers")
    if not isinstance(raw_workers, Mapping) or not raw_workers:
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid")

    credentials: dict[str, WorkerRegistrationCredential] = {}
    seen_urls: set[str] = set()
    seen_tokens: list[str] = []
    seen_worker_secret_fingerprints: set[str] = set()
    for raw_worker_id, raw_entry in raw_workers.items():
        worker_id = _bounded_identifier(raw_worker_id)
        if not worker_id or not isinstance(raw_entry, Mapping):
            raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid")
        worker_url = _normalize_worker_url(raw_entry.get("worker_url"))
        registration_token = _validated_token(raw_entry.get("registration_token"))
        service_token_sha256 = _validated_sha256(raw_entry.get("service_token_sha256"))
        session_signing_key_sha256 = _validated_sha256(raw_entry.get("session_signing_key_sha256"))
        raw_capabilities = raw_entry.get("allowed_capabilities")
        if (
            not isinstance(raw_capabilities, list)
            or isinstance(raw_capabilities, (str, bytes))
            or len(raw_capabilities) > 128
        ):
            raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid")
        allowed_capabilities = tuple(
            sorted({capability for value in raw_capabilities if (capability := _bounded_capability(value))})
        )
        if (
            not worker_url
            or not registration_token
            or not service_token_sha256
            or not session_signing_key_sha256
            or not allowed_capabilities
            or len(allowed_capabilities) != len(set(map(str, raw_capabilities)))
            or worker_url in seen_urls
            or service_token_sha256 in seen_worker_secret_fingerprints
            or session_signing_key_sha256 in seen_worker_secret_fingerprints
            or service_token_sha256 == session_signing_key_sha256
        ):
            raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid")
        if any(secrets.compare_digest(registration_token, existing) for existing in seen_tokens):
            raise WorkflowWorkerAuthConfigurationError("workflow_worker_registration_keyring_invalid")
        seen_urls.add(worker_url)
        seen_tokens.append(registration_token)
        seen_worker_secret_fingerprints.update({service_token_sha256, session_signing_key_sha256})
        credentials[worker_id] = WorkerRegistrationCredential(
            worker_id=worker_id,
            worker_url=worker_url,
            registration_token=registration_token,
            service_token_sha256=service_token_sha256,
            session_signing_key_sha256=session_signing_key_sha256,
            allowed_capabilities=allowed_capabilities,
        )
    return credentials


def worker_registration_keyring_configured(
    config: Mapping[str, Any] | None = None,
) -> bool:
    source = config or {}
    return bool(str(source.get(_KEYRING_FILE_ENV) or os.environ.get(_KEYRING_FILE_ENV) or "").strip())


def runtime_service_keyring_configured(
    config: Mapping[str, Any] | None = None,
) -> bool:
    source = config or {}
    return bool(
        str(
            source.get(_RUNTIME_SERVICE_KEYRING_FILE_ENV) or os.environ.get(_RUNTIME_SERVICE_KEYRING_FILE_ENV) or ""
        ).strip()
    )


def load_runtime_service_keyring(
    config: Mapping[str, Any] | None = None,
) -> dict[str, RuntimeServiceCredential]:
    source = config or {}
    path = str(
        source.get(_RUNTIME_SERVICE_KEYRING_FILE_ENV) or os.environ.get(_RUNTIME_SERVICE_KEYRING_FILE_ENV) or ""
    ).strip()
    if not path:
        raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_required")
    try:
        raw = read_file_managed_bytes(
            path,
            description="workflow runtime service keyring file",
            max_bytes=_MAX_KEYRING_BYTES,
        )
        decoded = json.loads(raw.decode("utf-8"))
    except (FileCredentialConfigurationError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid") from exc
    if not isinstance(decoded, Mapping) or decoded.get("schema") != RUNTIME_SERVICE_KEYRING_SCHEMA:
        raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid")
    raw_services = decoded.get("services")
    if not isinstance(raw_services, Mapping) or not raw_services:
        raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid")

    credentials: dict[str, RuntimeServiceCredential] = {}
    seen_tokens: list[str] = []
    for raw_service_id, raw_entry in raw_services.items():
        service_id = _bounded_identifier(raw_service_id)
        if not service_id or not isinstance(raw_entry, Mapping):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid")
        token = _validated_token(raw_entry.get("token"))
        raw_scopes = raw_entry.get("scopes")
        if not token or not isinstance(raw_scopes, list) or isinstance(raw_scopes, (str, bytes)):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid")
        scopes = tuple(sorted({str(value).strip() for value in raw_scopes if str(value).strip()}))
        if not scopes or any(scope not in _RUNTIME_SERVICE_SCOPES for scope in scopes):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid")
        if any(secrets.compare_digest(token, existing) for existing in seen_tokens):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_keyring_invalid")
        seen_tokens.append(token)
        credentials[service_id] = RuntimeServiceCredential(
            service_id=service_id,
            token=token,
            scopes=scopes,
        )
    return credentials


def validate_workflow_credential_disjointness(
    *,
    user_session_secret: str | None,
    hub_service_token: str | None,
    worker_service_tokens: Iterable[str],
    config: Mapping[str, Any] | None = None,
) -> None:
    """Reject credential reuse across every Hub/Worker/runtime trust domain."""

    strict = registered_worker_auth_required(config)
    runtime_configured = runtime_service_keyring_configured(config)
    if not strict and not runtime_configured:
        return

    registration_credentials = (
        tuple(load_worker_registration_keyring(config).values())
        if strict or worker_registration_keyring_configured(config)
        else ()
    )
    runtime_credentials = tuple(load_runtime_service_keyring(config).values()) if runtime_configured else ()
    entries: list[tuple[str, str]] = []
    session_secret = str(user_session_secret or "")
    if session_secret:
        entries.append(("user_session", _credential_sha256(session_secret)))
    if hub_token := _validated_token(hub_service_token):
        entries.append(("hub_service", _credential_sha256(hub_token)))
    actual_worker_digests = [
        _credential_sha256(token) for value in worker_service_tokens if (token := _validated_token(value))
    ]
    if len(actual_worker_digests) != len(set(actual_worker_digests)):
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_service_token_ambiguous")
    entries.extend(("worker_service", digest) for digest in actual_worker_digests)
    entries.extend(("worker_service", credential.service_token_sha256) for credential in registration_credentials)
    entries.extend(("worker_session", credential.session_signing_key_sha256) for credential in registration_credentials)
    entries.extend(
        (
            "worker_registration",
            _credential_sha256(credential.registration_token),
        )
        for credential in registration_credentials
    )
    entries.extend(("runtime_service", _credential_sha256(credential.token)) for credential in runtime_credentials)

    entries = list(dict.fromkeys(entries))

    for index, (left_domain, left_token) in enumerate(entries):
        for right_domain, right_token in entries[index + 1 :]:
            if secrets.compare_digest(left_token, right_token):
                raise WorkflowWorkerAuthConfigurationError(_credential_reuse_reason(left_domain, right_domain))


def _credential_reuse_reason(left_domain: str, right_domain: str) -> str:
    domains = frozenset({left_domain, right_domain})
    session_reasons = {
        "hub_service": "workflow_hub_session_hub_service_credential_reuse_denied",
        "worker_service": "workflow_hub_session_worker_service_credential_reuse_denied",
        "worker_registration": ("workflow_hub_session_worker_registration_credential_reuse_denied"),
        "worker_session": ("workflow_hub_session_worker_session_credential_reuse_denied"),
        "runtime_service": ("workflow_hub_session_runtime_service_credential_reuse_denied"),
    }
    if "user_session" in domains:
        other = next(domain for domain in domains if domain != "user_session")
        return session_reasons.get(
            other,
            "workflow_hub_session_credential_reuse_denied",
        )
    if left_domain == right_domain == "worker_service":
        return "workflow_worker_service_token_ambiguous"
    cross_domain_reasons = {
        frozenset({"hub_service", "worker_service"}): ("workflow_worker_service_admin_credential_reuse_denied"),
        frozenset({"hub_service", "worker_registration"}): (
            "workflow_worker_registration_hub_admin_credential_reuse_denied"
        ),
        frozenset({"hub_service", "runtime_service"}): ("workflow_runtime_service_admin_credential_reuse_denied"),
        frozenset({"worker_service", "worker_registration"}): (
            "workflow_worker_service_registration_credential_reuse_denied"
        ),
        frozenset({"worker_service", "runtime_service"}): ("workflow_runtime_service_worker_credential_reuse_denied"),
        frozenset({"worker_registration", "runtime_service"}): (
            "workflow_runtime_service_registration_credential_reuse_denied"
        ),
    }
    if reason := cross_domain_reasons.get(domains):
        return reason
    return "workflow_cross_domain_credential_reuse_denied"


def authenticate_preconfigured_runtime_service(
    provided_token: str,
    *,
    required_scope: str,
    claimed_service_id: str,
    forbidden_token: str | None = None,
    forbidden_tokens: Iterable[str] = (),
    forbidden_user_session_secret: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> RuntimeServiceCredential:
    if required_scope not in _RUNTIME_SERVICE_SCOPES:
        raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_scope_invalid")
    token = _validated_token(provided_token)
    service_id = _bounded_identifier(claimed_service_id)
    if not token:
        raise WorkflowWorkerAuthDenied("workflow_runtime_service_token_invalid")
    if not service_id:
        raise WorkflowWorkerAuthDenied("workflow_runtime_service_identity_required")
    keyring = load_runtime_service_keyring(config)
    worker_service_tokens = tuple(forbidden_tokens)
    validate_workflow_credential_disjointness(
        user_session_secret=forbidden_user_session_secret,
        hub_service_token=forbidden_token,
        worker_service_tokens=worker_service_tokens,
        config=config,
    )
    registration_tokens = (
        tuple(item.registration_token for item in load_worker_registration_keyring(config).values())
        if worker_registration_keyring_configured(config)
        else ()
    )
    _assert_runtime_credentials_disjoint(
        keyring.values(),
        hub_service_token=forbidden_token,
        worker_service_tokens=worker_service_tokens,
        worker_registration_tokens=registration_tokens,
        user_session_secret=forbidden_user_session_secret,
    )
    credential = keyring.get(service_id)
    if credential is None or not secrets.compare_digest(credential.token, token):
        raise WorkflowWorkerAuthDenied("workflow_runtime_service_identity_mismatch")
    if required_scope not in credential.scopes:
        raise WorkflowWorkerAuthDenied(
            "workflow_runtime_service_scope_forbidden",
            status_code=403,
        )
    return credential


def _assert_runtime_credentials_disjoint(
    runtime_credentials: Iterable[RuntimeServiceCredential],
    *,
    hub_service_token: str | None,
    worker_service_tokens: Iterable[str],
    worker_registration_tokens: Iterable[str],
    user_session_secret: str | None = None,
) -> None:
    """Validate the complete runtime keyring against every other trust domain."""

    runtime_tokens = tuple(item.token for item in runtime_credentials)
    service_tokens = tuple(token for value in worker_service_tokens if (token := _validated_token(value)))
    registration_tokens = tuple(token for value in worker_registration_tokens if (token := _validated_token(value)))
    hub_token = _validated_token(hub_service_token)
    session_secret = str(user_session_secret or "")
    for runtime_token in runtime_tokens:
        if session_secret and secrets.compare_digest(runtime_token, session_secret):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_user_session_credential_reuse_denied")
        if hub_token and secrets.compare_digest(runtime_token, hub_token):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_admin_credential_reuse_denied")
        if any(secrets.compare_digest(runtime_token, worker_token) for worker_token in service_tokens):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_worker_credential_reuse_denied")
        if any(secrets.compare_digest(runtime_token, registration_token) for registration_token in registration_tokens):
            raise WorkflowWorkerAuthConfigurationError("workflow_runtime_service_registration_credential_reuse_denied")


def validate_strict_worker_registration(
    data: Mapping[str, Any],
    *,
    registered_agents: Iterable[Any],
    hub_service_token: str | None,
    user_session_secret: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> WorkerRegistrationCredential:
    """Authenticate one registration and prevent cross-identity overwrite."""

    agents = tuple(registered_agents)

    if str(data.get("role") or "worker").strip().lower() != "worker":
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_role_required",
            status_code=403,
        )
    worker_id = _bounded_identifier(data.get("name"))
    worker_url = _normalize_worker_url(data.get("url"))
    service_token = _validated_token(data.get("token"))
    bootstrap_token = _validated_token(data.get("registration_token"))
    if not worker_id or not worker_url or not service_token or not bootstrap_token:
        raise WorkflowWorkerAuthDenied("workflow_worker_registration_credential_invalid")

    keyring = load_worker_registration_keyring(config)
    credential = keyring.get(worker_id)
    if (
        credential is None
        or credential.worker_url != worker_url
        or not secrets.compare_digest(
            credential.registration_token,
            bootstrap_token,
        )
    ):
        raise WorkflowWorkerAuthDenied("workflow_worker_registration_identity_denied")
    raw_capabilities = data.get("capabilities")
    if (
        not isinstance(raw_capabilities, list)
        or isinstance(raw_capabilities, (str, bytes))
        or len(raw_capabilities) > 128
    ):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_capabilities_invalid",
            status_code=400,
        )
    requested_capabilities = tuple(
        sorted({capability for value in raw_capabilities if (capability := _bounded_capability(value))})
    )
    if len(requested_capabilities) != len(set(map(str, raw_capabilities))):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_capabilities_invalid",
            status_code=400,
        )
    if not set(requested_capabilities).issubset(credential.allowed_capabilities):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_capability_escalation_denied",
            status_code=403,
        )
    if secrets.compare_digest(service_token, bootstrap_token):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_secret_reuse_denied",
            status_code=409,
        )
    session_secret = str(user_session_secret or "")
    if session_secret and secrets.compare_digest(bootstrap_token, session_secret):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_user_session_credential_reuse_denied",
            status_code=409,
        )
    if session_secret and secrets.compare_digest(service_token, session_secret):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_service_user_session_credential_reuse_denied",
            status_code=409,
        )
    if hub_service_token and secrets.compare_digest(bootstrap_token, hub_service_token):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_hub_admin_credential_reuse_denied",
            status_code=409,
        )
    if hub_service_token and secrets.compare_digest(service_token, hub_service_token):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_hub_admin_credential_reuse_denied",
            status_code=409,
        )
    registration_tokens = (item.registration_token for item in keyring.values())
    if any(secrets.compare_digest(service_token, registration_token) for registration_token in registration_tokens):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_service_registration_credential_reuse_denied",
            status_code=409,
        )
    if runtime_service_keyring_configured(config):
        runtime_credentials = tuple(load_runtime_service_keyring(config).values())
        runtime_tokens = tuple(item.token for item in runtime_credentials)
        if any(secrets.compare_digest(service_token, runtime_token) for runtime_token in runtime_tokens):
            raise WorkflowWorkerAuthDenied(
                "workflow_worker_runtime_credential_reuse_denied",
                status_code=409,
            )
        if any(secrets.compare_digest(bootstrap_token, runtime_token) for runtime_token in runtime_tokens):
            raise WorkflowWorkerAuthDenied(
                "workflow_worker_registration_runtime_credential_reuse_denied",
                status_code=409,
            )
        _assert_runtime_credentials_disjoint(
            runtime_credentials,
            hub_service_token=hub_service_token,
            worker_service_tokens=(str(getattr(agent, "token", "") or "") for agent in agents),
            worker_registration_tokens=(item.registration_token for item in keyring.values()),
            user_session_secret=session_secret,
        )

    other_service_tokens: list[str] = []
    for agent in agents:
        existing_id = str(getattr(agent, "name", "") or "").strip()
        existing_url = _normalize_worker_url(getattr(agent, "url", ""))
        existing_token = _validated_token(getattr(agent, "token", None))
        same_identity = existing_id == worker_id and existing_url == worker_url
        if (existing_id == worker_id or existing_url == worker_url) and not same_identity:
            raise WorkflowWorkerAuthDenied(
                "workflow_worker_registration_identity_conflict",
                status_code=409,
            )
        if existing_token and secrets.compare_digest(existing_token, bootstrap_token):
            raise WorkflowWorkerAuthDenied(
                "workflow_worker_registration_service_credential_reuse_denied",
                status_code=409,
            )
        if existing_token and not same_identity and secrets.compare_digest(existing_token, service_token):
            raise WorkflowWorkerAuthDenied(
                "workflow_worker_service_token_conflict",
                status_code=409,
            )
        if existing_token and not same_identity:
            other_service_tokens.append(existing_token)
    if not secrets.compare_digest(
        _credential_sha256(service_token),
        credential.service_token_sha256,
    ):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_service_token_fingerprint_mismatch",
            status_code=401,
        )
    validate_workflow_credential_disjointness(
        user_session_secret=session_secret,
        hub_service_token=hub_service_token,
        worker_service_tokens=(
            *other_service_tokens,
            service_token,
        ),
        config=config,
    )
    return credential


def authenticate_registered_workflow_worker(
    provided_token: str,
    *,
    required_scope: str,
    claimed_worker_id: str,
    claimed_worker_url: str,
    registered_agents: Iterable[Any],
    hub_service_token: str | None = None,
    user_session_secret: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> RegisteredWorkflowWorkerIdentity:
    """Resolve one raw bearer to exactly one registered Worker and scope."""

    if required_scope not in _SCOPE_CAPABILITIES:
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_service_scope_invalid")
    token = _validated_token(provided_token)
    worker_id = _bounded_identifier(claimed_worker_id)
    worker_url = _normalize_worker_url(claimed_worker_url)
    if not token:
        raise WorkflowWorkerAuthDenied("workflow_worker_service_token_invalid")
    if not worker_id or not worker_url:
        raise WorkflowWorkerAuthDenied("workflow_worker_service_identity_required")

    agents = tuple(registered_agents)
    validate_workflow_credential_disjointness(
        user_session_secret=user_session_secret,
        hub_service_token=hub_service_token,
        worker_service_tokens=(str(getattr(agent, "token", "") or "") for agent in agents),
        config=config,
    )
    matches: list[Any] = []
    for agent in agents:
        candidate = _validated_token(getattr(agent, "token", None))
        if candidate and secrets.compare_digest(candidate, token):
            matches.append(agent)
    if len(matches) != 1:
        reason = (
            "workflow_worker_service_token_ambiguous" if len(matches) > 1 else "workflow_worker_service_token_invalid"
        )
        raise WorkflowWorkerAuthDenied(reason)

    agent = matches[0]
    actual_id = _bounded_identifier(getattr(agent, "name", ""))
    actual_url = _normalize_worker_url(getattr(agent, "url", ""))
    if (
        str(getattr(agent, "role", "") or "").strip().lower() != "worker"
        or not bool(getattr(agent, "registration_validated", False))
        or str(getattr(agent, "registration_provenance", "") or "") != STRICT_WORKER_REGISTRATION_PROVENANCE
        or str(getattr(agent, "status", "") or "").strip().lower()
        not in {"online", "degraded", "busy"}
        or not actual_id
        or not actual_url
    ):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_not_validated",
            status_code=403,
        )
    if actual_id != worker_id or actual_url != worker_url:
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_service_identity_mismatch",
            status_code=403,
        )

    capabilities = tuple(
        sorted(
            {
                str(value).strip()
                for value in (getattr(agent, "authorized_capabilities", None) or [])
                if str(value).strip()
            }
        )
    )
    keyring = load_worker_registration_keyring(config)
    registration = keyring.get(actual_id)
    if (
        registration is None
        or registration.worker_url != actual_url
        or not secrets.compare_digest(
            registration.service_token_sha256,
            _credential_sha256(token),
        )
        or capabilities != registration.allowed_capabilities
    ):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_registration_provenance_mismatch",
            status_code=403,
        )
    if hub_service_token and secrets.compare_digest(token, hub_service_token):
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_service_admin_credential_reuse_denied")
    if user_session_secret and secrets.compare_digest(token, user_session_secret):
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_service_user_session_credential_reuse_denied")
    if any(secrets.compare_digest(token, item.registration_token) for item in keyring.values()):
        raise WorkflowWorkerAuthConfigurationError("workflow_worker_service_registration_credential_reuse_denied")
    if runtime_service_keyring_configured(config):
        _assert_runtime_credentials_disjoint(
            load_runtime_service_keyring(config).values(),
            hub_service_token=hub_service_token,
            worker_service_tokens=(str(getattr(item, "token", "") or "") for item in agents),
            worker_registration_tokens=(item.registration_token for item in keyring.values()),
            user_session_secret=user_session_secret,
        )
    scope_capabilities = _SCOPE_CAPABILITIES[required_scope]
    if scope_capabilities and not set(capabilities).intersection(
        scope_capabilities
    ):
        raise WorkflowWorkerAuthDenied(
            "workflow_worker_service_scope_forbidden",
            status_code=403,
        )
    return RegisteredWorkflowWorkerIdentity(
        worker_id=actual_id,
        worker_url=actual_url,
        capabilities=capabilities,
    )


def _validated_token(raw: object) -> str:
    token = str(raw or "").strip()
    encoded = token.encode("utf-8")
    if (
        not _MIN_TOKEN_BYTES <= len(encoded) <= _MAX_TOKEN_BYTES
        or "\x00" in token
        or any(character.isspace() for character in token)
    ):
        return ""
    return token


def _credential_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_sha256(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return ""
    return value


def _bounded_identifier(raw: object) -> str:
    value = str(raw or "").strip()
    if (
        not value
        or len(value.encode("utf-8")) > _MAX_IDENTIFIER_BYTES
        or "\x00" in value
        or any(character in value for character in "\r\n")
    ):
        return ""
    return value


def _bounded_capability(raw: object) -> str:
    value = _bounded_identifier(raw)
    if not value or len(value) > 128 or any(character.isspace() for character in value):
        return ""
    return value


def _normalize_worker_url(raw: object) -> str:
    value = str(raw or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    hostname = str(parsed.hostname).lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


__all__ = [
    "RegisteredWorkflowWorkerIdentity",
    "RUNTIME_SERVICE_ID_HEADER",
    "RUNTIME_SERVICE_KEYRING_SCHEMA",
    "RECOVERY_TASK_DISPATCH_SCOPE",
    "RECOVERY_TASK_MANIFEST_SCOPE",
    "RECOVERY_ARTIFACT_INGRESS_SCOPE",
    "STRICT_WORKER_REGISTRATION_PROVENANCE",
    "WORKER_ID_HEADER",
    "WORKER_REGISTRATION_KEYRING_SCHEMA",
    "WORKER_URL_HEADER",
    "WORKFLOW_LANGGRAPH_CHECKPOINT_SCOPE",
    "KNOWLEDGE_INDEX_PAYLOAD_SCOPE",
    "KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCOPE",
    "SEMANTIC_COMPUTE_WORKER_SCOPE",
    "SPEECH_EVIDENCE_CURATION_WORKER_SCOPE",
    "WORKFLOW_TEMPORAL_TASK_SCOPE",
    "WORKFLOW_WORKER_COMMAND_SCOPE",
    "VECTOR_INDEX_DISPATCH_ADMISSION_SCOPE",
    "WorkflowWorkerAuthConfigurationError",
    "WorkflowWorkerAuthDenied",
    "authenticate_registered_workflow_worker",
    "authenticate_preconfigured_runtime_service",
    "load_runtime_service_keyring",
    "load_worker_registration_keyring",
    "registered_worker_auth_required",
    "runtime_service_keyring_configured",
    "validate_strict_worker_registration",
    "validate_workflow_credential_disjointness",
    "worker_registration_keyring_configured",
]
