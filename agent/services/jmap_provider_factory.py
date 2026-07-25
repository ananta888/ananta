from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from agent.services.jmap_auth_service import JmapAuthService
from agent.services.jmap_discovery_service import (
    JmapCapabilitiesService,
    JmapDiscoveryCache,
    JmapDiscoveryService,
    JmapLifecycleService,
    JmapSessionRegistry,
)
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy
from agent.services.jmap_http_transport import JmapHttpTransport
from agent.services.jmap_mail_mutation_provider import JmapMailMutationProvider
from agent.services.jmap_mail_read_provider import JmapMailReadProvider
from agent.services.jmap_sync_service import JmapSyncService
from agent.services.mail_body_service import MailBodyService
from agent.services.mail_contract_service import MailAccountV2, MailMessageRefV2
from agent.services.mail_domain_mapper import MailboxLocatorResolver, MailDomainMapper
from agent.services.mail_feature_policy import MailFeaturePolicy, MailRuntimeAvailabilityPolicy
from agent.services.mail_mutation_policy import MailMutationPolicy
from agent.services.mail_provider_ports import (
    MailAttachment,
    MailAuthMaterial,
    MailBody,
    MailDeleteRequest,
    MailKeywordChange,
    MailMailbox,
    MailMessage,
    MailMoveRequest,
    MailMutationReport,
    MailProviderBinding,
    MailProviderResult,
    MailProviderSession,
    MailQuery,
    MailQueryPage,
    MailSyncCursor,
    MailSyncDelta,
    VerifiedMailContentAccess,
)
from agent.services.mail_sync_state_store import MailSyncStateStore


@dataclass(frozen=True, slots=True)
class JmapProviderDependencies:
    transport: JmapHttpTransport
    endpoint_policy: JmapEndpointPolicy
    auth_service: JmapAuthService
    feature_policy: MailFeaturePolicy
    sync_state_store: MailSyncStateStore
    mutation_policy: MailMutationPolicy
    mailbox_locator_resolver: MailboxLocatorResolver
    availability_policy: MailRuntimeAvailabilityPolicy


class _AvailabilityGate:
    def __init__(
        self,
        *,
        account_id: str,
        policy: MailRuntimeAvailabilityPolicy,
    ) -> None:
        self._account_id = str(account_id)
        self._policy = policy

    def execute(
        self,
        *,
        operation: str,
        callback: Callable[[], MailProviderResult[Any]],
    ) -> MailProviderResult[Any]:
        try:
            decision = self._policy.evaluate(
                account_id=self._account_id,
                provider="jmap",
                operation=operation,
            )
        except Exception:
            return MailProviderResult(ok=False, reason_code="mail_runtime_policy_unavailable")
        if not isinstance(decision, MailProviderResult):
            return MailProviderResult(ok=False, reason_code="mail_runtime_policy_unavailable")
        if not decision.ok:
            return MailProviderResult(
                ok=False,
                reason_code=decision.reason_code,
                retryable=decision.retryable,
                retry_after_ms=decision.retry_after_ms,
                details=decision.details,
            )
        result = callback()
        try:
            if result.ok:
                self._policy.record_success(
                    account_id=self._account_id,
                    provider="jmap",
                    operation=operation,
                )
            else:
                self._policy.record_failure(
                    account_id=self._account_id,
                    provider="jmap",
                    operation=operation,
                    retryable=result.retryable,
                )
        except Exception:
            if result.ok:
                return MailProviderResult(ok=False, reason_code="mail_runtime_policy_unavailable")
        return result


class _AvailableLifecycle:
    def __init__(
        self,
        *,
        account: MailAccountV2,
        delegate: JmapLifecycleService,
        gate: _AvailabilityGate,
    ) -> None:
        self._account = account
        self._delegate = delegate
        self._gate = gate

    def connect(
        self,
        account: MailAccountV2,
        auth: MailAuthMaterial,
    ) -> MailProviderResult[MailProviderSession]:
        if account.account_id != self._account.account_id:
            return MailProviderResult(ok=False, reason_code="mail_provider_account_mismatch")
        return self._gate.execute(
            operation="discovery",
            callback=lambda: self._delegate.connect(account, auth),
        )

    def disconnect(self, session: MailProviderSession) -> MailProviderResult[None]:
        if session.account_id != self._account.account_id:
            return MailProviderResult(ok=False, reason_code="mail_provider_session_account_mismatch")
        return self._delegate.disconnect(session)


class _BoundRuntime:
    def __init__(
        self,
        *,
        account: MailAccountV2,
        registry: JmapSessionRegistry,
        gate: _AvailabilityGate,
    ) -> None:
        self.account = account
        self.registry = registry
        self.gate = gate

    def execute(
        self,
        session: MailProviderSession,
        *,
        operation: str,
        callback: Callable[[Any], MailProviderResult[Any]],
    ) -> MailProviderResult[Any]:
        if session.account_id != self.account.account_id or session.protocol != "jmap":
            return MailProviderResult(ok=False, reason_code="mail_provider_session_account_mismatch")

        def connected() -> MailProviderResult[Any]:
            client = self.registry.client(session)
            if not client.ok or client.value is None:
                return MailProviderResult(
                    ok=False,
                    reason_code=client.reason_code,
                    retryable=client.retryable,
                    retry_after_ms=client.retry_after_ms,
                )
            return callback(client.value)

        return self.gate.execute(operation=operation, callback=connected)


class _BoundReadPort:
    def __init__(
        self,
        *,
        runtime: _BoundRuntime,
        mapper: MailDomainMapper,
        mailboxes: MailboxLocatorResolver,
    ) -> None:
        self._runtime = runtime
        self._mapper = mapper
        self._mailboxes = mailboxes

    def _provider(self, client: Any) -> JmapMailReadProvider:
        return JmapMailReadProvider(
            client=client,
            local_account_id=self._runtime.account.account_id,
            mapper=self._mapper,
            mailbox_locator_resolver=self._mailboxes,
        )

    def list_mailboxes(
        self,
        session: MailProviderSession,
    ) -> MailProviderResult[tuple[MailMailbox, ...]]:
        return self._runtime.execute(
            session,
            operation="read",
            callback=lambda client: self._provider(client).list_mailboxes(session),
        )

    def query_messages(
        self,
        session: MailProviderSession,
        query: MailQuery,
    ) -> MailProviderResult[MailQueryPage]:
        return self._runtime.execute(
            session,
            operation="read",
            callback=lambda client: self._provider(client).query_messages(session, query),
        )

    def get_messages(
        self,
        session: MailProviderSession,
        ids: Sequence[str],
        properties: Sequence[str] = (),
    ) -> MailProviderResult[tuple[MailMessage, ...]]:
        return self._runtime.execute(
            session,
            operation="read",
            callback=lambda client: self._provider(client).get_messages(session, ids, properties),
        )


class _BoundBodyPort:
    def __init__(self, *, runtime: _BoundRuntime, maximum_body_bytes: int) -> None:
        self._runtime = runtime
        self._maximum_body_bytes = maximum_body_bytes

    def _provider(self, client: Any) -> MailBodyService:
        return MailBodyService(
            client=client,
            local_account_id=self._runtime.account.account_id,
            maximum_body_bytes=self._maximum_body_bytes,
        )

    def get_body(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[MailBody]:
        return self._runtime.execute(
            session,
            operation="body",
            callback=lambda client: self._provider(client).get_body(
                session,
                message_ref,
                access=access,
            ),
        )

    def get_attachments(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[tuple[MailAttachment, ...]]:
        return self._runtime.execute(
            session,
            operation="body",
            callback=lambda client: self._provider(client).get_attachments(
                session,
                message_ref,
                access=access,
            ),
        )


class _BoundSyncPort:
    def __init__(
        self,
        *,
        runtime: _BoundRuntime,
        state_store: MailSyncStateStore,
        mapper: MailDomainMapper,
        feature_policy: MailFeaturePolicy,
    ) -> None:
        self._runtime = runtime
        self._state_store = state_store
        self._mapper = mapper
        self._feature_policy = feature_policy

    def sync(
        self,
        session: MailProviderSession,
        cursor: MailSyncCursor | None,
        policy: str,
    ) -> MailProviderResult[MailSyncDelta]:
        return self._runtime.execute(
            session,
            operation="sync",
            callback=lambda client: JmapSyncService(
                client=client,
                local_account_id=self._runtime.account.account_id,
                state_store=self._state_store,
                mapper=self._mapper,
                limits=self._feature_policy.limits,
            ).sync(session, cursor, policy),
        )


class _BoundMutationPort:
    def __init__(
        self,
        *,
        runtime: _BoundRuntime,
        mutation_policy: MailMutationPolicy,
        mailboxes: MailboxLocatorResolver,
    ) -> None:
        self._runtime = runtime
        self._mutation_policy = mutation_policy
        self._mailboxes = mailboxes

    def _provider(self, client: Any) -> JmapMailMutationProvider:
        return JmapMailMutationProvider(
            client=client,
            local_account_id=self._runtime.account.account_id,
            policy=self._mutation_policy,
            mailbox_locator_resolver=self._mailboxes,
        )

    def set_keywords(
        self,
        session: MailProviderSession,
        changes: Sequence[MailKeywordChange],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return self._runtime.execute(
            session,
            operation="mutation",
            callback=lambda client: self._provider(client).set_keywords(
                session,
                changes,
                if_in_state=if_in_state,
            ),
        )

    def move_messages(
        self,
        session: MailProviderSession,
        moves: Sequence[MailMoveRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return self._runtime.execute(
            session,
            operation="mutation",
            callback=lambda client: self._provider(client).move_messages(
                session,
                moves,
                if_in_state=if_in_state,
            ),
        )

    def delete_messages(
        self,
        session: MailProviderSession,
        deletes: Sequence[MailDeleteRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        return self._runtime.execute(
            session,
            operation="mutation",
            callback=lambda client: self._provider(client).delete_messages(
                session,
                deletes,
                if_in_state=if_in_state,
            ),
        )


class JmapProviderBindingBuilder:
    def __init__(self, *, dependencies: JmapProviderDependencies) -> None:
        self._dependencies = dependencies

    def build(self, account: MailAccountV2) -> MailProviderResult[MailProviderBinding]:
        if not account.enabled:
            return MailProviderResult(ok=False, reason_code="mail_account_disabled")
        if account.effective_protocol != "jmap":
            return MailProviderResult(ok=False, reason_code="mail_provider_protocol_mismatch")
        dependencies = self._dependencies
        registry = JmapSessionRegistry(transport=dependencies.transport)
        discovery_cache = JmapDiscoveryCache(
            ttl_seconds=dependencies.feature_policy.limits.discovery_cache_ttl_seconds
        )
        discovery = JmapDiscoveryService(
            transport=dependencies.transport,
            endpoint_policy=dependencies.endpoint_policy,
            auth_service=dependencies.auth_service,
            feature_policy=dependencies.feature_policy,
            cache=discovery_cache,
            session_state_observer=registry.invalidate_account_state,
        )
        lifecycle = JmapLifecycleService(
            discovery=discovery,
            auth_service=dependencies.auth_service,
            registry=registry,
        )
        gate = _AvailabilityGate(
            account_id=account.account_id,
            policy=dependencies.availability_policy,
        )
        runtime = _BoundRuntime(account=account, registry=registry, gate=gate)
        mapper = MailDomainMapper()
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=MailProviderBinding(
                protocol="jmap",
                lifecycle=_AvailableLifecycle(
                    account=account,
                    delegate=lifecycle,
                    gate=gate,
                ),
                capabilities=JmapCapabilitiesService(registry=registry),
                reader=_BoundReadPort(
                    runtime=runtime,
                    mapper=mapper,
                    mailboxes=dependencies.mailbox_locator_resolver,
                ),
                body=_BoundBodyPort(
                    runtime=runtime,
                    maximum_body_bytes=min(
                        1024 * 1024,
                        dependencies.feature_policy.limits.maximum_json_response_bytes,
                    ),
                ),
                mutator=_BoundMutationPort(
                    runtime=runtime,
                    mutation_policy=dependencies.mutation_policy,
                    mailboxes=dependencies.mailbox_locator_resolver,
                ),
                sync=_BoundSyncPort(
                    runtime=runtime,
                    state_store=dependencies.sync_state_store,
                    mapper=mapper,
                    feature_policy=dependencies.feature_policy,
                ),
            ),
        )


class JmapProviderFactory:
    def __init__(self, *, builder: JmapProviderBindingBuilder) -> None:
        self._builder = builder

    @classmethod
    def from_dependencies(
        cls,
        dependencies: JmapProviderDependencies,
    ) -> "JmapProviderFactory":
        return cls(builder=JmapProviderBindingBuilder(dependencies=dependencies))

    def create(self, account: MailAccountV2) -> MailProviderResult[MailProviderBinding]:
        return self._builder.build(account)


__all__ = [
    "JmapProviderBindingBuilder",
    "JmapProviderDependencies",
    "JmapProviderFactory",
]
