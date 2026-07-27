"""Provider-neutral Hub application service for mail operator surfaces."""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_artifact_service import MailArtifactService
from agent.services.mail_contract_service import (
    MailAccountV2,
    MailMessageMetadata,
    MailMessageRefV2,
    stable_mail_ref_id,
)
from agent.services.mail_metadata_store_service import MailMetadataStore
from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailProviderResult,
    VerifiedMailContentAccess,
)
from agent.services.mail_task_service import MailWorkspaceScope, get_mail_task_service


class MailApplicationError(ValueError):
    """Stable, non-sensitive error exposed to routes and the operator TUI."""


class LegacyMailBridge(Protocol):
    """Small compatibility port for the existing IMAP-only implementation."""

    def list_accounts(self) -> Sequence[Mapping[str, Any]]: ...

    def disable_account(self, account_id: str) -> Any: ...

    def delete_account(self, account_id: str) -> Any: ...

    def list_message_metadata(self) -> Sequence[Mapping[str, Any]]: ...

    def load_body(self, message: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def load_attachment(
        self,
        message: Mapping[str, Any],
        attachment_id: str,
    ) -> Mapping[str, Any]: ...


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    raise MailApplicationError("mail_value_is_not_serializable")


def _result_value(result: Any, operation: str) -> Any:
    if not isinstance(result, MailProviderResult):
        return result
    if result.ok:
        return result.value
    raise MailApplicationError(result.reason_code or f"{operation}_failed")


_SENSITIVE_FIELDS = {
    "authorization",
    "credential",
    "credential_ref",
    "password",
    "secret",
    "token",
    "username",
    "username_ref",
    "protocol_locator",
    "provider_locator",
    "session_state",
}


def _public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value


def _public_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(_public_value(value))


class _ExplicitOperatorAccessPolicy:
    """Issues a five-minute capability after explicit confirmation."""

    def authorize(
        self,
        request: MailContentAccessRequest,
    ) -> MailProviderResult[MailContentAccessDecision]:
        now = datetime.now(UTC)
        nonce_material = "|".join(
            (
                request.account_id,
                request.workspace_id,
                request.artifact_ref,
                request.mail_ref_id,
                request.grant_ref,
                request.release_scope,
                now.isoformat(),
            )
        )
        return MailProviderResult.success(
            MailContentAccessDecision(
                allowed=True,
                reason_code="mail_operator_explicit_access",
                policy_decision_ref="policy:mail:operator-explicit:v1",
                expires_at=(now + timedelta(minutes=5))
                .isoformat()
                .replace("+00:00", "Z"),
                nonce=hashlib.sha256(nonce_material.encode("utf-8")).hexdigest(),
            ),
            reason_code="mail_operator_explicit_access",
        )


class NullLegacyMailBridge:
    def list_accounts(self) -> Sequence[Mapping[str, Any]]:
        return ()

    def disable_account(self, account_id: str) -> Any:
        raise MailApplicationError("mail_account_not_found")

    def delete_account(self, account_id: str) -> Any:
        raise MailApplicationError("mail_account_not_found")

    def list_message_metadata(self) -> Sequence[Mapping[str, Any]]:
        return ()

    def load_body(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        raise MailApplicationError("mail_body_not_cached")

    def load_attachment(
        self,
        message: Mapping[str, Any],
        attachment_id: str,
    ) -> Mapping[str, Any]:
        raise MailApplicationError("mail_attachment_not_cached")


class DynamicLegacyMailBridge(NullLegacyMailBridge):
    """Lazy adapter; JMAP-only installations never import the IMAP stack."""

    def __init__(self, *, repo_root: Path | str | None = None) -> None:
        self._repo_root = Path(repo_root or ".").resolve()

    @staticmethod
    def _factory(module_name: str, names: Sequence[str]) -> Any | None:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return None
        for name in names:
            factory = getattr(module, name, None)
            if callable(factory):
                return factory()
        return None

    @staticmethod
    def _call(component: Any, names: Sequence[str], *args: Any) -> Any:
        if component is None:
            raise MailApplicationError("legacy_mail_component_unavailable")
        for name in names:
            operation = getattr(component, name, None)
            if callable(operation):
                return operation(*args)
        raise MailApplicationError("legacy_mail_operation_unavailable")

    def _accounts(self) -> Any | None:
        try:
            return importlib.import_module("agent.services.imap_account_service")
        except ImportError:
            return None

    def _store(self) -> Any | None:
        try:
            module = importlib.import_module(
                "agent.services.imap_metadata_store_service"
            )
        except ImportError:
            return None
        store_type = getattr(module, "ImapMetadataStore", None)
        if not callable(store_type):
            return None
        return store_type(
            store_path=self._repo_root
            / "data"
            / "imap"
            / "mail-metadata.json"
        )

    def list_accounts(self) -> Sequence[Mapping[str, Any]]:
        component = self._accounts()
        if component is None:
            return ()
        return tuple(
            _mapping(item)
            for item in component.list_imap_accounts(repo_root=self._repo_root)
        )

    def disable_account(self, account_id: str) -> Any:
        component = self._accounts()
        if component is None:
            raise MailApplicationError("legacy_mail_component_unavailable")
        return component.disable_imap_account(
            account_id=account_id,
            repo_root=self._repo_root,
        )

    def delete_account(self, account_id: str) -> Any:
        component = self._accounts()
        if component is None:
            raise MailApplicationError("legacy_mail_component_unavailable")
        return component.delete_imap_account(
            account_id=account_id,
            repo_root=self._repo_root,
        )

    def list_message_metadata(self) -> Sequence[Mapping[str, Any]]:
        component = self._store()
        if component is None:
            return ()
        rows = []
        for item in component.list_messages():
            row = dict(item)
            rows.append(
                {
                    **dict(row.get("header_meta") or {}),
                    **dict(row.get("message_ref") or {}),
                    "attachments": list(row.get("attachments") or []),
                    "stale": bool(row.get("stale", False)),
                }
            )
        return tuple(rows)

    def load_body(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        component = self._store()
        if component is None:
            raise MailApplicationError("legacy_mail_component_unavailable")
        result = component.get_by_uid(
            account_id=str(message.get("account_id") or ""),
            mailbox=str(message.get("mailbox") or ""),
            uid=int(message.get("uid") or 0),
        )
        if not isinstance(result, Mapping) or not str(result.get("body") or ""):
            raise MailApplicationError("mail_body_not_cached")
        return {
            "text": str(result.get("body") or ""),
            "html": "",
            "scope": str(result.get("body_scope") or "metadata_only"),
        }

    def load_attachment(
        self,
        message: Mapping[str, Any],
        attachment_id: str,
    ) -> Mapping[str, Any]:
        component = self._store()
        if component is None:
            raise MailApplicationError("legacy_mail_component_unavailable")
        row = component.get_by_uid(
            account_id=str(message.get("account_id") or ""),
            mailbox=str(message.get("mailbox") or ""),
            uid=int(message.get("uid") or 0),
        )
        for attachment in dict(row or {}).get("attachments") or ():
            if (
                isinstance(attachment, Mapping)
                and str(
                    attachment.get("attachment_id")
                    or attachment.get("part_id")
                    or attachment.get("filename")
                    or ""
                )
                == attachment_id
            ):
                from agent.services.imap_attachment_service import (
                    download_attachment_securely,
                )

                return download_attachment_securely(
                    attachment=dict(attachment),
                    target_dir=self._repo_root
                    / "data"
                    / "imap"
                    / "attachment-downloads",
                )
        raise MailApplicationError("mail_attachment_not_cached")


class MailApplicationService:
    """Hub-owned use cases shared by HTTP and TUI surfaces."""

    def __init__(
        self,
        *,
        account_service: MailAccountService,
        metadata_store: MailMetadataStore,
        legacy: LegacyMailBridge | None = None,
        content_access_verifier: MailContentAccessVerifier | None = None,
        task_service_factory: Callable[[], Any] = get_mail_task_service,
        artifact_service: MailArtifactService | None = None,
        repo_root: Path | str | None = None,
    ) -> None:
        self._accounts = account_service
        self._metadata = metadata_store
        self._legacy = legacy or DynamicLegacyMailBridge()
        self._access_verifier = content_access_verifier or MailContentAccessVerifier(
            policy=_ExplicitOperatorAccessPolicy()
        )
        self._task_service_factory = task_service_factory
        self._artifacts = artifact_service or MailArtifactService(
            store_path=Path("data/mail/artifacts-v2.json")
        )
        self._repo_root = Path(repo_root or ".").resolve()

    def list_accounts(self) -> list[dict[str, Any]]:
        from agent.services.mail_runtime_policy import (
            get_mail_circuit_breaker,
            get_mail_health_registry,
            get_mail_runtime_policy,
        )

        runtime = get_mail_runtime_policy().snapshot()
        runtime_payload = runtime.to_dict()
        health = get_mail_health_registry().snapshot()
        circuit = get_mail_circuit_breaker()
        public: list[dict[str, Any]] = []
        for account in self._accounts.list_accounts():
            row = _public_mapping(account.to_dict())
            provider = account.effective_protocol or ""
            row["protocol"] = provider or account.requested_protocol
            row["provider"] = provider or "unresolved"
            row["requested_protocol"] = account.requested_protocol
            row["resolved_protocol"] = account.resolved_protocol
            row["runtime_phase"] = runtime_payload.get("phase")
            row["runtime_state"] = (
                "online"
                if runtime_payload.get("network_enabled") and account.enabled
                else "offline"
            )
            row["connection_state"] = (
                "not_connected" if account.enabled else "disabled"
            )
            row["circuit_state"] = (
                circuit.state(
                    account_id=account.account_id,
                    provider=provider,
                )
                if provider in {"jmap", "imap"}
                else "not_applicable"
            )
            cursor = (
                self._metadata.get_sync_cursor(
                    account_id=account.account_id,
                    protocol=provider,
                    scope="default",
                )
                if provider in {"jmap", "imap"}
                else None
            )
            row["sync_state"] = (
                {
                    "status": "synced",
                    "email_state": cursor.email_state,
                    "query_state": cursor.query_state,
                    "mailbox_state": cursor.mailbox_state,
                }
                if cursor is not None
                else {"status": "never"}
            )
            row["health"] = health
            public.append(row)
        known = {str(row.get("account_id", "")) for row in public}
        for account in self._legacy.list_accounts():
            row = _public_mapping(account)
            account_id = str(row.get("account_id") or row.get("id") or "")
            if account_id and account_id not in known:
                row.setdefault("account_id", account_id)
                row.setdefault("protocol", "imap")
                row.setdefault("runtime_state", "offline")
                public.append(row)
        for row in public:
            row["last_task"] = self._last_task_summary(
                str(row.get("account_id", ""))
            )
            last_task = dict(row.get("last_task") or {})
            operation = str(last_task.get("operation") or "")
            status = str(last_task.get("status") or "")
            result = dict(last_task.get("result") or {})
            if operation == "discovery":
                row["discovery_state"] = status or "not_started"
            else:
                row.setdefault("discovery_state", "not_started")
            if status == "completed" and result.get("provider"):
                row["provider"] = str(result["provider"])
                row["connection_state"] = "last_connection_succeeded"
            elif status == "failed":
                row["connection_state"] = "last_connection_failed"
            if operation == "sync" and status:
                row["sync_state"] = {
                    **dict(row.get("sync_state") or {}),
                    "status": status,
                    "last_task_id": last_task.get("job_id"),
                }
        return public

    def preview_account(
        self,
        *,
        account_id: str,
        display_name: str,
        requested_protocol: str,
        username_ref: str,
        credential_ref: str,
        sync_policy: str = "manual",
        provider_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        protocol = requested_protocol.strip().lower()
        if protocol not in {"auto", "jmap", "imap"}:
            raise MailApplicationError("mail_protocol_invalid")
        account = MailAccountV2(
            account_id=account_id.strip(),
            display_name=display_name.strip(),
            requested_protocol=protocol,
            username_ref=username_ref.strip(),
            credential_ref=credential_ref.strip(),
            sync_policy=sync_policy.strip(),
            enabled=False,
            provider_config=dict(provider_config or {}),
        )
        draft = account.to_dict()
        MailAccountV2.from_mapping(draft)
        return {
            "account": _public_mapping(draft),
            "confirmation_required": True,
            "discovery_required": protocol == "auto",
            "draft": draft,
        }

    def request_discovery(
        self,
        *,
        preview: Mapping[str, Any],
        workspace: MailWorkspaceScope,
        idempotency_key: str,
        actor_ref: str,
    ) -> dict[str, Any]:
        account = MailAccountV2.from_mapping(preview)
        self._accounts.upsert_account(account)
        task = self._task_service_factory().submit(
            operation="discovery",
            account_ref=f"mail-account:{account.account_id}",
            workspace_scope=workspace,
            idempotency_key=idempotency_key,
            actor=actor_ref,
            policy_refs={"discovery_policy_ref": "policy:mail:discovery:v1"},
        )
        return _mapping(task)

    def confirm_account(
        self,
        *,
        preview: Mapping[str, Any],
        resolved_protocol: str | None = None,
        discovery_task_id: str | None = None,
    ) -> dict[str, Any]:
        account = MailAccountV2.from_mapping(preview)
        protocol = str(resolved_protocol or account.resolved_protocol or "").lower()
        if account.requested_protocol == "auto" and protocol not in {"jmap", "imap"}:
            raise MailApplicationError("mail_discovery_confirmation_required")
        if account.requested_protocol in {"jmap", "imap"}:
            protocol = account.requested_protocol
        confirmed = MailAccountV2(
            account_id=account.account_id,
            display_name=account.display_name,
            requested_protocol=account.requested_protocol,
            resolved_protocol=protocol,
            username_ref=account.username_ref,
            credential_ref=account.credential_ref,
            sync_policy=account.sync_policy,
            enabled=True,
            provider_config=account.provider_config,
        )
        self._accounts.upsert_account(confirmed)
        result = _public_mapping(confirmed.to_dict())
        result["discovery_task_id"] = discovery_task_id
        return result

    def disable_account(self, account_id: str) -> dict[str, Any]:
        try:
            return _public_mapping(
                self._accounts.disable_account(account_id).to_dict()
            )
        except ValueError as exc:
            if str(exc) != "mail_account_not_found":
                raise
            return _public_mapping(
                _mapping(self._legacy.disable_account(account_id))
            )

    def delete_account(self, account_id: str) -> dict[str, Any]:
        account = self._accounts.get_account(account_id)
        if account is not None:
            if account.enabled:
                raise MailApplicationError("mail_account_v2_disable_required")
            return _public_mapping(
                self._accounts.delete_account(account_id).to_dict()
            )
        return _public_mapping(_mapping(self._legacy.delete_account(account_id)))

    def list_message_metadata(
        self,
        *,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            {**dict(item.get("metadata") or {}), **dict(item.get("message_ref") or {})}
            for item in self._metadata.list_messages(account_id=account_id)
        ]
        rows = self.sanitize_message_metadata_rows(rows)
        rows.extend(
            self.sanitize_message_metadata_rows(
                self._legacy.list_message_metadata(),
                default_protocol="imap",
            )
        )
        if account_id:
            rows = [
                row
                for row in rows
                if str(row.get("account_id", "")) == account_id
            ]
        return rows

    def search_message_metadata(
        self,
        query: str,
        *,
        account_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        rows = self.list_message_metadata(account_id=account_id)
        if not needle:
            return rows[: max(0, limit)]
        searchable = ("subject", "from", "from_address", "message_id_header")
        return [
            row
            for row in rows
            if any(needle in str(row.get(key, "")).casefold() for key in searchable)
        ][: max(0, limit)]

    def thread_message_metadata(self, mail_ref_id: str) -> list[dict[str, Any]]:
        rows = self.list_message_metadata()
        selected = next(
            (row for row in rows if row.get("mail_ref_id") == mail_ref_id),
            None,
        )
        if selected is None:
            raise MailApplicationError("mail_message_not_found")
        thread_ref = selected.get("thread_ref_id")
        if not thread_ref:
            return [selected]
        return [row for row in rows if row.get("thread_ref_id") == thread_ref]

    def attachment_metadata(self, mail_ref_id: str) -> list[dict[str, Any]]:
        attachments = self.get_message_metadata(mail_ref_id).get("attachments")
        if not isinstance(attachments, list):
            return []
        return [
            _public_mapping(item)
            for item in attachments
            if isinstance(item, Mapping)
        ]

    def get_message_metadata(self, mail_ref_id: str) -> dict[str, Any]:
        for row in self.list_message_metadata():
            if row.get("mail_ref_id") == mail_ref_id:
                return row
        raise MailApplicationError("mail_message_not_found")

    def get_provider_message_ref(self, mail_ref_id: str) -> MailMessageRefV2:
        """Resolve a canonical id to a provider locator inside the Hub only."""
        stored = self._metadata.get_by_mail_ref_id(mail_ref_id)
        if isinstance(stored, Mapping):
            raw = stored.get("message_ref")
            if isinstance(raw, Mapping):
                return MailMessageRefV2.from_mapping(raw)
        row = self.get_message_metadata(mail_ref_id)
        if (
            str(row.get("protocol") or "") == "imap"
            and row.get("mailbox")
            and row.get("uid") is not None
        ):
            return MailMessageRefV2(
                mail_ref_id=mail_ref_id,
                account_id=str(row.get("account_id") or ""),
                protocol="imap",
                protocol_locator={
                    "mailbox": str(row.get("mailbox") or ""),
                    "uid": int(row.get("uid") or 0),
                    "uidvalidity": row.get("uidvalidity"),
                },
                locator_version=1,
                thread_ref_id=str(row.get("thread_ref_id") or ""),
            )
        raise MailApplicationError("mail_provider_message_ref_not_found")

    def authorize_operator_content(
        self,
        *,
        mail_ref_id: str,
        account_id: str,
        workspace_id: str,
        artifact_ref: str,
        grant_ref: str,
        release_scope: str,
        explicit_confirmation: bool,
    ) -> VerifiedMailContentAccess:
        if not explicit_confirmation:
            raise MailApplicationError("mail_content_confirmation_required")
        access = _result_value(
            self._access_verifier.verify(
                MailContentAccessRequest(
                    account_id=account_id,
                    workspace_id=workspace_id,
                    artifact_ref=artifact_ref,
                    mail_ref_id=mail_ref_id,
                    grant_ref=grant_ref,
                    release_scope=release_scope,
                )
            ),
            "mail_content_access",
        )
        if not isinstance(access, VerifiedMailContentAccess):
            raise MailApplicationError("mail_content_access_unverified")
        return access

    def load_body(
        self,
        mail_ref_id: str,
        *,
        access: VerifiedMailContentAccess,
    ) -> Mapping[str, Any]:
        row = self.get_message_metadata(mail_ref_id)
        self._assert_access(row, mail_ref_id, access, {"body_excerpt", "full_body"})
        if row.get("protocol") == "imap" and row.get("uid") is not None:
            body = dict(self._legacy.load_body(row))
        else:
            stored = self._metadata.get_by_mail_ref_id(mail_ref_id)
            cached = dict(stored or {}).get("body")
            if not isinstance(cached, Mapping) or not cached:
                raise MailApplicationError("mail_body_not_cached")
            body = dict(cached)
        from agent.services.imap_security_policy_service import redact_mail_content

        redacted = redact_mail_content(
            str(body.get("text") or body.get("body_text") or "")
        )
        return {
            **body,
            "text": redacted["text"],
            "redaction_status": redacted["redaction_status"],
            "redaction_reason_code": redacted["reason_code"],
        }

    def load_attachment(
        self,
        mail_ref_id: str,
        attachment_id: str,
        *,
        access: VerifiedMailContentAccess,
    ) -> Mapping[str, Any]:
        row = self.get_message_metadata(mail_ref_id)
        self._assert_access(row, mail_ref_id, access, {"attachment_ref"})
        if row.get("protocol") == "imap" and row.get("uid") is not None:
            return self._legacy.load_attachment(row, attachment_id)
        stored = self._metadata.get_by_mail_ref_id(mail_ref_id)
        for attachment in dict(stored or {}).get("attachments") or ():
            if (
                isinstance(attachment, Mapping)
                and str(attachment.get("attachment_id", "")) == attachment_id
            ):
                return dict(attachment)
        raise MailApplicationError("mail_attachment_not_cached")

    def sanitize_message_metadata_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        default_protocol: str = "jmap",
    ) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            account_id = str(row.get("account_id") or "")
            protocol = str(row.get("protocol") or default_protocol).lower()
            stable_identity = (
                row.get("protocol_locator")
                or row.get("message_id_header")
                or row.get("message_id")
                or row.get("uid")
                or row.get("mail_ref_id")
                or ""
            )
            mail_ref_id = str(row.get("mail_ref_id") or "")
            if not mail_ref_id and account_id and stable_identity:
                mail_ref_id = stable_mail_ref_id(
                    account_id=account_id,
                    protocol=protocol,
                    stable_identity=stable_identity,
                )
            public = _public_mapping(row)
            public["mail_ref_id"] = mail_ref_id
            public["protocol"] = protocol
            if protocol != "imap":
                public.pop("uid", None)
                public.pop("mailbox", None)
            for field in ("body", "raw", "content"):
                public.pop(field, None)
            sanitized.append(public)
        return sanitized

    def register_artifact(
        self,
        *,
        message_ref: Mapping[str, Any],
        scope: str,
        redaction_status: str,
        policy_decision_ref: str,
        excerpt: str = "",
        access: VerifiedMailContentAccess | None = None,
    ) -> dict[str, Any]:
        ref = dict(message_ref)
        if (
            str(ref.get("protocol") or "").lower() == "imap"
            and ref.get("mailbox")
            and ref.get("uid") is not None
        ):
            if scope != "metadata_only":
                if not isinstance(access, VerifiedMailContentAccess):
                    raise PermissionError("verified_mail_content_access_required")
                if (
                    access.account_id != str(ref.get("account_id") or "")
                    or access.mail_ref_id != str(ref.get("mail_ref_id") or "")
                    or not access.artifact_ref.startswith(
                        f"mail://{str(ref.get('mail_ref_id') or '')}"
                    )
                ):
                    raise PermissionError("mail_content_artifact_mismatch")
            from agent.services.imap_mail_artifact_service import (
                register_mail_artifact,
            )

            return register_mail_artifact(
                message_ref=ref,
                scope=scope,
                redaction_status=redaction_status,
                policy_decision_ref=policy_decision_ref,
                excerpt=excerpt,
                repo_root=self._repo_root,
            )
        return self._artifacts.register(
            message_ref=message_ref,
            scope=scope,
            redaction_status=redaction_status,
            policy_decision_ref=policy_decision_ref,
            excerpt=excerpt,
            access=access,
        )

    def export_message(
        self,
        *,
        message_ref: Mapping[str, Any],
        header_meta: Mapping[str, Any],
        body_text: str,
        format_name: str,
        include_body: bool,
        access: VerifiedMailContentAccess | None = None,
    ) -> dict[str, Any]:
        from agent.services.mail_export_service import MailExportService

        ref = dict(message_ref)
        return MailExportService().export(
            message_ref=MailMessageRefV2(
                mail_ref_id=str(ref.get("mail_ref_id") or ""),
                account_id=str(ref.get("account_id") or ""),
                protocol=str(ref.get("protocol") or "imap"),
                protocol_locator={},
                locator_version=1,
                thread_ref_id=str(ref.get("thread_ref_id") or ""),
            ),
            metadata=MailMessageMetadata.from_mapping(header_meta),
            body_text=body_text,
            format_name=format_name,
            include_body=include_body,
            export_dir=self._artifacts.store_path.parent / "exports",
            access=access,
        )

    @staticmethod
    def explain_for_snake(
        *,
        opened: bool,
        artifact_ref: str,
        message_ref: Mapping[str, Any],
        body_text: str,
    ) -> dict[str, Any]:
        from agent.services.imap_snake_assist_service import (
            explain_mail_for_snake_assist,
        )

        return explain_mail_for_snake_assist(
            opened=opened,
            artifact_ref=artifact_ref,
            message_ref=dict(message_ref),
            body_text=body_text,
        )

    def build_context_envelope(
        self,
        *,
        goal_id: str,
        worker_target: str,
    ) -> dict[str, Any]:
        from agent.services.imap_mail_context_envelope_service import (
            build_mail_context_envelope,
        )

        legacy = build_mail_context_envelope(
            goal_id=goal_id,
            worker_target=worker_target,
            repo_root=str(self._repo_root),
        )
        if legacy.get("mail_source_refs") or not bool(legacy.get("allowed")):
            return legacy
        return self._artifacts.build_context_envelope(
            goal_id=goal_id,
            worker_target=worker_target,
        )

    @staticmethod
    def _assert_access(
        row: Mapping[str, Any],
        mail_ref_id: str,
        access: VerifiedMailContentAccess,
        allowed_scopes: set[str],
    ) -> None:
        if not isinstance(access, VerifiedMailContentAccess):
            raise MailApplicationError("mail_content_access_unverified")
        if (
            access.account_id != str(row.get("account_id") or "")
            or access.mail_ref_id != mail_ref_id
            or not access.artifact_ref.startswith(f"mail://{mail_ref_id}")
            or access.release_scope not in allowed_scopes
        ):
            raise MailApplicationError("mail_content_access_scope_mismatch")

    def _last_task_summary(self, account_id: str) -> dict[str, Any] | None:
        if not account_id:
            return None
        try:
            task = self._task_service_factory().last_task_for_account(
                f"mail-account:{account_id}"
            )
        except Exception:
            return None
        return _mapping(task) if task is not None else None


_instances: dict[str, MailApplicationService] = {}
_instances_lock = RLock()


def get_mail_application_service(
    *,
    root: Path | str | None = None,
) -> MailApplicationService:
    base = Path(root or ".").resolve()
    key = str(base)
    with _instances_lock:
        service = _instances.get(key)
        if service is None:
            service = MailApplicationService(
                account_service=MailAccountService(
                    store_path=base / "data" / "mail" / "accounts-v2.json"
                ),
                metadata_store=MailMetadataStore(
                    store_path=base / "data" / "mail" / "metadata-v2.json"
                ),
                legacy=DynamicLegacyMailBridge(repo_root=base),
                artifact_service=MailArtifactService(
                    store_path=base / "data" / "mail" / "artifacts-v2.json"
                ),
                repo_root=base,
            )
            _instances[key] = service
        return service


__all__ = [
    "DynamicLegacyMailBridge",
    "LegacyMailBridge",
    "MailApplicationError",
    "MailApplicationService",
    "NullLegacyMailBridge",
    "get_mail_application_service",
]
