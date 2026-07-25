"""Production composition root for delegated mail tasks."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from agent.services.imap_connector_service import ImapConnectorService
from agent.adapters.mail_metrics_adapter import MailMetricsAdapter
from agent.services.imap_provider_adapter import ImapProviderAdapter
from agent.services.jmap_auth_service import JmapAuthService
from agent.services.jmap_endpoint_policy import (
    JmapEndpointPolicy,
    JmapEndpointPolicyConfig,
)
from agent.services.jmap_http_transport import JmapHttpTransport
from agent.services.jmap_provider_factory import (
    JmapProviderDependencies,
    JmapProviderFactory,
)
from agent.services.mail_account_service import MailAccountService
from agent.services.mail_feature_policy import MailFeaturePolicy
from agent.services.mail_mutation_policy import (
    MailMutationAuthorization,
    MailMutationPolicy,
    VerifiedIntentMutationAuthorizer,
)
from agent.services.mail_provider_ports import MailProviderResult
from agent.services.mail_provider_router import MailProviderRouter
from agent.services.mail_runtime_policy import (
    get_mail_health_registry,
    get_mail_runtime_availability_policy,
)
from agent.services.mail_runtime_state_adapters import (
    PersistentMailboxLocatorStore,
)
from agent.services.mail_secret_resolver import (
    EnvFileMailSecretResolver,
    MailAccountAuthResolver,
)
from worker.imap_runtime_client import SecureImapClientFactory
from worker.mail_operation_intent_client import (
    HttpMailOperationIntentClient,
    ResolvedMailMutationIntentVerifier,
    ResolvedMailOperationIntent,
)
from worker.mail_provider_task_execution import ProviderMailTaskExecution
from worker.mail_sync_transaction_adapter import (
    build_transactional_mail_runtime_state,
)

_LOGGER = logging.getLogger(__name__)


class _ObservedMailTaskExecution:
    def __init__(self, *, delegate: Any, metrics: Any, health: Any) -> None:
        self._delegate = delegate
        self._metrics = metrics
        self._health = health

    def execute(self, **kwargs: Any) -> Any:
        import time

        started = time.monotonic()
        outcome = self._delegate.execute(**kwargs)
        operation = str(kwargs.get("operation") or "diagnose")
        provider = outcome.provider or "none"
        success = outcome.status == "completed"
        reason = str(outcome.reason_code or "")
        error_class = "none"
        for candidate, markers in {
            "auth": ("auth", "credential", "secret"),
            "timeout": ("timeout", "deadline"),
            "transport": ("connection", "network", "http", "dns"),
            "policy": ("policy", "disabled", "denied", "forbidden"),
            "store": ("store", "persist", "database"),
            "protocol": ("jmap", "imap", "protocol"),
            "config": ("config", "endpoint"),
        }.items():
            if any(marker in reason for marker in markers):
                error_class = candidate
                break
        self._metrics.record_call(
            provider=provider,
            operation=operation,
            outcome="success" if success else "failure",
            error_class=error_class,
            duration_seconds=time.monotonic() - started,
        )
        if outcome.retryable:
            self._metrics.record_retry(
                provider=provider,
                operation=operation,
                error_class=error_class,
            )
        if operation == "sync" and success:
            counters = dict(outcome.counters or {})
            for kind in ("created", "updated", "destroyed"):
                self._metrics.record_sync_changes(
                    provider=provider,
                    change_kind=kind,
                    count=int(counters.get(f"{kind}_count") or 0),
                )
        component = {
            "discovery": "discovery",
            "diagnose": "discovery",
            "sync": "sync",
            "body": "api",
            "mutation": "api",
        }.get(operation, "config")
        self._health.observe(
            component,
            status="ok" if success else "degraded",
            reason_code=(
                f"mail_{component}_available"
                if success
                else (
                    reason
                    if reason.startswith("mail_")
                    and reason.replace("_", "").isalnum()
                    else f"mail_{component}_failed"
                )
            ),
        )
        return outcome


def _bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    value = str(source.get(name) or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name.lower()}_invalid")


def _csv(source: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in str(source.get(name) or "").split(",")
        if value.strip()
    )


class _DeniedMutationIntentVerifier:
    def verify(
        self,
        request: MailMutationAuthorization,
    ) -> MailProviderResult[None]:
        del request
        return MailProviderResult.failure("mail_mutation_intent_required")


class _SafeMutationAuditSink:
    def record(
        self,
        *,
        account_id: str,
        operation: str,
        intent_ref: str,
        audit_ref: str,
        outcome: str,
        reason_code: str,
    ) -> None:
        _LOGGER.info(
            "mail_mutation_audit account_id=%s operation=%s intent_ref=%s "
            "audit_ref=%s outcome=%s reason_code=%s",
            account_id,
            operation,
            intent_ref,
            audit_ref,
            outcome,
            reason_code,
        )


def build_production_mail_task_execution(
    *,
    environ: Mapping[str, str] | None = None,
    runtime_availability: Any | None = None,
    sync_fault_injector: Callable[[str], None] | None = None,
) -> Any:
    source = dict(os.environ if environ is None else environ)
    repo_root = Path(source.get("ANANTA_REPO_ROOT") or ".").resolve()
    data_root = Path(
        source.get("ANANTA_MAIL_DATA_ROOT")
        or repo_root / "data" / "mail"
    ).resolve()
    transactional_state = build_transactional_mail_runtime_state(
        database_path=data_root / "runtime-state-v1.sqlite3",
        fault_injector=sync_fault_injector,
    )
    metadata = transactional_state.metadata_store
    mailboxes = PersistentMailboxLocatorStore(
        store_path=data_root / "mailbox-locators-v1.json"
    )
    sync_state = transactional_state.sync_state_store
    feature_policy = MailFeaturePolicy(
        mail_enabled=_bool(source, "ANANTA_MAIL_ENABLED", True),
        jmap_enabled=_bool(source, "ANANTA_MAIL_JMAP_ENABLED", True),
        imap_fallback_enabled=_bool(
            source,
            "ANANTA_MAIL_IMAP_FALLBACK_ENABLED",
            True,
        ),
        protocol_autodiscovery_enabled=_bool(
            source,
            "ANANTA_MAIL_AUTODISCOVERY_ENABLED",
            True,
        ),
        external_network_enabled=_bool(
            source,
            "ANANTA_MAIL_EXTERNAL_NETWORK_ENABLED",
            False,
        ),
        local_endpoint_policy_enabled=_bool(
            source,
            "ANANTA_MAIL_LOCAL_ENDPOINTS_ENABLED",
            False,
        ),
    )
    endpoint_policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(
            external_network_enabled=feature_policy.external_network_enabled,
            local_endpoints_enabled=feature_policy.local_endpoint_policy_enabled,
            allowed_related_origins=_csv(
                source,
                "ANANTA_MAIL_ALLOWED_RELATED_ORIGINS",
            ),
            allowed_local_hosts=_csv(
                source,
                "ANANTA_MAIL_ALLOWED_LOCAL_HOSTS",
            ),
            allowed_local_cidrs=_csv(
                source,
                "ANANTA_MAIL_ALLOWED_LOCAL_CIDRS",
            ),
        )
    )
    metrics = MailMetricsAdapter()
    transport = JmapHttpTransport(
        endpoint_policy=endpoint_policy,
        limits=feature_policy.limits,
        observer=metrics,
    )
    availability = (
        runtime_availability
        if runtime_availability is not None
        else get_mail_runtime_availability_policy()
    )
    audit_sink = _SafeMutationAuditSink()

    def router_factory(
        intent: ResolvedMailOperationIntent | None,
    ) -> MailProviderRouter:
        verifier: Any = (
            ResolvedMailMutationIntentVerifier(intent=intent)
            if intent is not None and intent.operation == "mutation"
            else _DeniedMutationIntentVerifier()
        )
        mutation_policy = MailMutationPolicy(
            authorizer=VerifiedIntentMutationAuthorizer(verifier=verifier),
            audit_sink=audit_sink,
        )
        jmap = JmapProviderFactory.from_dependencies(
            JmapProviderDependencies(
                transport=transport,
                endpoint_policy=endpoint_policy,
                auth_service=JmapAuthService(),
                feature_policy=feature_policy,
                sync_state_store=sync_state,
                mutation_policy=mutation_policy,
                mailbox_locator_resolver=mailboxes,
                availability_policy=availability,
            )
        )
        imap = ImapProviderAdapter(
            connector=ImapConnectorService(
                client_factory=SecureImapClientFactory(
                    endpoint_policy=endpoint_policy,
                    connect_timeout_seconds=feature_policy.limits.connect_timeout_seconds,
                    maximum_body_bytes=feature_policy.limits.maximum_blob_bytes,
                )
            )
        )
        factories: dict[str, Any] = {}
        if feature_policy.jmap_enabled:
            factories["jmap"] = jmap
        if feature_policy.imap_fallback_enabled:
            factories["imap"] = imap
        return MailProviderRouter(factories)

    secret_roots = _csv(source, "ANANTA_MAIL_SECRET_ROOTS") or (
        "/run/secrets",
    )
    secret_resolver = EnvFileMailSecretResolver(
        environ=source,
        allowed_file_roots=secret_roots,
    )
    hub_url = str(
        source.get("ANANTA_MAIL_HUB_URL")
        or source.get("HUB_URL")
        or "http://localhost:5000"
    ).strip()
    hub_token = str(
        source.get("ANANTA_MAIL_HUB_TOKEN")
        or source.get("ANANTA_AUTH_TOKEN")
        or ""
    ).strip()
    intent_client = (
        HttpMailOperationIntentClient(
            hub_url=hub_url,
            token=hub_token,
            timeout_seconds=feature_policy.limits.read_timeout_seconds,
        )
        if hub_token
        else None
    )
    execution = ProviderMailTaskExecution(
        accounts=MailAccountService(
            store_path=data_root / "accounts-v2.json"
        ),
        auth=MailAccountAuthResolver(
            username_resolver=secret_resolver,
            credential_resolver=secret_resolver,
        ),
        router_factory=router_factory,
        metadata=metadata,
        mailboxes=mailboxes,
        intents=intent_client,
        imap_availability=availability,
    )
    return _ObservedMailTaskExecution(
        delegate=execution,
        metrics=metrics,
        health=get_mail_health_registry(),
    )


__all__ = ["build_production_mail_task_execution"]
