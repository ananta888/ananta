"""Worker-to-Hub client for resolving opaque mail operation intents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailProviderResult,
)
from agent.services.mail_mutation_policy import MailMutationAuthorization


@dataclass(frozen=True)
class ResolvedMailOperationIntent:
    intent_ref: str
    operation: str
    account_id: str
    workspace_id: str
    grant_ref: str
    payload: Mapping[str, Any]
    expires_at: float
    job_id: str


class MailOperationIntentClient(Protocol):
    def resolve(
        self,
        *,
        intent_ref: str,
        job_id: str,
        operation: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
    ) -> MailProviderResult[ResolvedMailOperationIntent]: ...

    def authorize_content(
        self,
        *,
        intent_ref: str,
        job_id: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
        request: MailContentAccessRequest,
    ) -> MailProviderResult[MailContentAccessDecision]: ...


class HttpMailOperationIntentClient:
    def __init__(
        self,
        *,
        hub_url: str,
        token: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._hub_url = str(hub_url or "").strip().rstrip("/")
        self._token = str(token or "").strip()
        self._timeout = max(1.0, min(float(timeout_seconds), 30.0))
        if not self._hub_url or not self._token:
            raise ValueError("mail_intent_hub_client_config_incomplete")

    def _post(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
    ) -> MailProviderResult[dict[str, Any]]:
        import requests

        session = requests.Session()
        session.trust_env = False
        try:
            response = session.post(
                f"{self._hub_url}{endpoint}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=dict(payload),
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            return MailProviderResult.failure(
                "mail_intent_hub_timeout",
                retryable=True,
            )
        except requests.RequestException:
            return MailProviderResult.failure(
                "mail_intent_hub_unavailable",
                retryable=True,
            )
        finally:
            session.close()
        try:
            body = response.json()
        except ValueError:
            return MailProviderResult.failure(
                "mail_intent_hub_response_invalid"
            )
        if not isinstance(body, Mapping):
            return MailProviderResult.failure(
                "mail_intent_hub_response_invalid"
            )
        if response.status_code != 200 or not bool(body.get("ok")):
            return MailProviderResult.failure(
                str(body.get("reason_code") or "mail_intent_hub_denied"),
                retryable=response.status_code >= 500,
            )
        return MailProviderResult.success(dict(body))

    def resolve(
        self,
        *,
        intent_ref: str,
        job_id: str,
        operation: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
    ) -> MailProviderResult[ResolvedMailOperationIntent]:
        response = self._post(
            "/api/mail/internal/intents/resolve",
            {
                "intent_ref": intent_ref,
                "job_id": job_id,
                "operation": operation,
                "account_ref": account_ref,
                "workspace_scope": dict(workspace_scope),
            },
        )
        raw = dict(response.value or {}).get("intent")
        if not response.ok or not isinstance(raw, Mapping):
            return MailProviderResult.failure(
                response.reason_code
                if not response.ok
                else "mail_operation_intent_response_invalid",
                retryable=response.retryable,
            )
        try:
            intent = ResolvedMailOperationIntent(
                intent_ref=str(raw["intent_ref"]),
                operation=str(raw["operation"]),
                account_id=str(raw["account_id"]),
                workspace_id=str(raw["workspace_id"]),
                grant_ref=str(raw["grant_ref"]),
                payload=dict(raw["payload"]),
                expires_at=float(raw["expires_at"]),
                job_id=str(raw["job_id"]),
            )
        except (KeyError, TypeError, ValueError):
            return MailProviderResult.failure(
                "mail_operation_intent_response_invalid"
            )
        return MailProviderResult.success(
            intent,
            reason_code="mail_operation_intent_resolved",
        )

    def authorize_content(
        self,
        *,
        intent_ref: str,
        job_id: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
        request: MailContentAccessRequest,
    ) -> MailProviderResult[MailContentAccessDecision]:
        response = self._post(
            "/api/mail/internal/intents/authorize-content",
            {
                "intent_ref": intent_ref,
                "job_id": job_id,
                "account_ref": account_ref,
                "workspace_scope": dict(workspace_scope),
                "access_request": {
                    "account_id": request.account_id,
                    "workspace_id": request.workspace_id,
                    "artifact_ref": request.artifact_ref,
                    "mail_ref_id": request.mail_ref_id,
                    "grant_ref": request.grant_ref,
                    "release_scope": request.release_scope,
                },
            },
        )
        raw = dict(response.value or {}).get("decision")
        if not response.ok or not isinstance(raw, Mapping):
            return MailProviderResult.failure(
                response.reason_code
                if not response.ok
                else "mail_content_decision_response_invalid",
                retryable=response.retryable,
            )
        return MailProviderResult.success(
            MailContentAccessDecision(
                allowed=bool(raw.get("allowed")),
                reason_code=str(raw.get("reason_code") or ""),
                policy_decision_ref=str(
                    raw.get("policy_decision_ref") or ""
                ),
                expires_at=str(raw.get("expires_at") or ""),
                nonce=str(raw.get("nonce") or ""),
            )
        )


class HubMailContentAccessPolicy:
    def __init__(
        self,
        *,
        client: MailOperationIntentClient,
        intent_ref: str,
        job_id: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
    ) -> None:
        self._client = client
        self._intent_ref = intent_ref
        self._job_id = job_id
        self._account_ref = account_ref
        self._workspace_scope = dict(workspace_scope)

    def authorize(
        self,
        request: MailContentAccessRequest,
    ) -> MailProviderResult[MailContentAccessDecision]:
        return self._client.authorize_content(
            intent_ref=self._intent_ref,
            job_id=self._job_id,
            account_ref=self._account_ref,
            workspace_scope=self._workspace_scope,
            request=request,
        )


class ResolvedMailMutationIntentVerifier:
    """Binds a mutation request to the exact Hub-resolved intent."""

    def __init__(self, *, intent: ResolvedMailOperationIntent) -> None:
        self._intent = intent

    def verify(
        self,
        request: MailMutationAuthorization,
    ) -> MailProviderResult[None]:
        payload = dict(self._intent.payload)
        action = str(payload.get("action") or "")
        expected_operation = {
            "set_keywords": "set_keywords",
            "move_messages": "move_messages",
            "delete_messages": (
                "permanent_delete"
                if bool(payload.get("permanent"))
                else "move_to_trash"
            ),
        }.get(action, "")
        if (
            self._intent.operation != "mutation"
            or request.account_id != self._intent.account_id
            or request.operation != expected_operation
            or request.intent_ref != str(payload.get("intent_ref") or "")
            or request.audit_ref != str(payload.get("audit_ref") or "")
            or request.confirmation_ref
            != str(payload.get("confirmation_ref") or "")
        ):
            return MailProviderResult.failure(
                "mail_mutation_intent_scope_mismatch"
            )
        return MailProviderResult.success(
            reason_code="mail_mutation_intent_verified"
        )


__all__ = [
    "HubMailContentAccessPolicy",
    "HttpMailOperationIntentClient",
    "MailOperationIntentClient",
    "ResolvedMailMutationIntentVerifier",
    "ResolvedMailOperationIntent",
]
