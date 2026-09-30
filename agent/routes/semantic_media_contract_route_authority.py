"""Share-membership, capability-grant and feature authority for the semantic-compute contract routes."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Callable

from flask import current_app, request

from agent.models.semantic_principal import SemanticPrincipal
from agent.services.semantic_contract_service import (
    SemanticContractServiceError,
    get_semantic_contract_service,
)
from agent.services.semantic_media_permission_service import (
    SemanticMediaPermissionError,
    SemanticMediaPermissionService,
)
from agent.services.share_session_permissions import (
    get_share_session_permission_service,
)
from agent.services.share_session_service import get_share_session_service
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from agent.routes.semantic_media_contract_route_parsing import (
    _bounded_int,
    _identifier,
    _optional_identifier,
)


_SEMANTIC_CONTROL_DATA_TYPE = "application/vnd.ananta.semantic-media-control+json"
_SEMANTIC_CONTROL_PURPOSE = "semantic_media_control"
_CAPABILITY_GRANT_HEADER = "X-Semantic-Capability-Grant"


class SemanticContractRouteAuthority:
    """Resolve share membership and capability issuance authority for one request.

    Service providers are injected so the Hub composition (or a test) decides
    which share-session, WebRTC epoch, contract and permission services act as
    authority; defaults are the production singletons.
    """

    def __init__(
        self,
        *,
        share_sessions: Callable[[], Any] = get_share_session_service,
        webrtc_epochs: Callable[[], Any] = get_webrtc_epoch_service,
        contracts: Callable[[], Any] = get_semantic_contract_service,
        share_permissions: Callable[[], Any] = get_share_session_permission_service,
    ) -> None:
        self._share_sessions = share_sessions
        self._webrtc_epochs = webrtc_epochs
        self._contracts = contracts
        self._share_permissions = share_permissions

    def establish_membership(self, principal: SemanticPrincipal, body: dict[str, Any]) -> None:
        session_id = _identifier(body.get("session_id"), "session_id")
        epoch = _bounded_int(body.get("epoch"), "epoch", 1, 2_147_483_647)
        share, _permissions = self.share_membership_authority(
            principal,
            session_id=session_id,
            epoch=epoch,
        )
        is_owner = str(share.get("owner_user_id") or "") == principal.subject
        role = "owner" if is_owner else "participant"
        expires_at = share.get("expires_at")
        # This record represents membership only.  Every user action is separately
        # admitted through a current, purpose-bound capability grant below.
        self._contracts().establish_membership(
            principal,
            session_id=session_id,
            epoch=epoch,
            role=role,
            permitted=True,
            room_id=_optional_identifier(body.get("room_id"), "room_id"),
            expires_at=float(expires_at) if isinstance(expires_at, (int, float)) else None,
        )


    def share_membership_authority(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        share = self._share_sessions().get_session(session_id)
        if not isinstance(share, dict) or share.get("revoked_at") is not None:
            raise SemanticContractServiceError("session_not_found", status_code=404)
        expires_at = share.get("expires_at")
        if isinstance(expires_at, (int, float)) and float(expires_at) <= time.time():
            raise SemanticContractServiceError("session_not_found", status_code=404)
        current_epoch = self._webrtc_epochs().current_epoch("session", session_id)
        if current_epoch is not None and current_epoch != epoch:
            raise SemanticContractServiceError("session_not_found", status_code=404)
        if str(share.get("owner_user_id") or "") == principal.subject:
            return share, dict(share.get("permissions") or {})
        participant = next(
            (
                item
                for item in self._share_sessions().get_participants(session_id)
                if str(item.get("user_id") or "") == principal.subject
                and item.get("revoked_at") is None
            ),
            None,
        )
        if participant is None:
            raise SemanticContractServiceError("session_not_found", status_code=404)
        return share, dict(participant.get("permissions") or {})


    def capability_issuance_authority(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        subject_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        share, owner_permissions = self.share_membership_authority(
            principal,
            session_id=session_id,
            epoch=epoch,
        )
        owner_id = str(share.get("owner_user_id") or "")
        if owner_id != principal.subject:
            raise SemanticMediaPermissionError("capability_issue_denied")
        if subject_id == owner_id:
            return share, owner_permissions
        participant = next(
            (
                item
                for item in self._share_sessions().get_participants(session_id)
                if str(item.get("user_id") or "") == subject_id
                and item.get("revoked_at") is None
            ),
            None,
        )
        if participant is None:
            raise SemanticMediaPermissionError("capability_subject_not_found", status_code=404)
        return share, dict(participant.get("permissions") or {})


    def attenuated_capabilities(
        self,
        session_id: str,
        raw_permissions: dict[str, Any],
        *,
        allow_training: bool,
    ) -> set[str]:
        permissions = self._share_permissions().effective(session_id, raw_permissions)
        capabilities: set[str] = set()
        if permissions.get("chat") is True:
            capabilities.update({"publish", "subscribe"})
        if permissions.get("view_tui") is True:
            capabilities.update({"capture", "publish", "subscribe"})
        if permissions.get("remote_cursor") is True:
            capabilities.add("publish")
        if permissions.get("remote_control") is True:
            capabilities.update({"compute", "validate"})
        if permissions.get("artifact_share") is True:
            capabilities.add("evidence_transfer")
        if allow_training:
            capabilities.add("training_admission")
        return capabilities


def _training_capability_authorised(
    principal: SemanticPrincipal,
    body: dict[str, Any],
) -> bool:
    """Use only a server-installed consent resolver; browser input is not authority."""

    resolver = current_app.extensions.get("semantic_media_training_capability_resolver")
    if not callable(resolver):
        return False
    return resolver(
        tenant_id=principal.tenant_id,
        owner_id=principal.subject,
        subject_id=str(body.get("subject_id") or ""),
        session_id=str(body.get("session_id") or ""),
        epoch=body.get("epoch"),
        purpose=str(body.get("purpose") or ""),
        data_type=str(body.get("data_type") or ""),
    ) is True


def _semantic_permission_service(*, required: bool) -> SemanticMediaPermissionService | None:
    service = current_app.extensions.get("semantic_media_permission_service")
    if isinstance(service, SemanticMediaPermissionService):
        return service
    if required:
        raise SemanticMediaPermissionError("capability_service_unavailable", status_code=503)
    return None


def _require_semantic_capability(
    principal: SemanticPrincipal,
    body: dict[str, Any],
    capability: str,
    *,
    direction: str,
) -> None:
    try:
        service = _semantic_permission_service(required=True)
    except SemanticMediaPermissionError as exc:
        raise SemanticContractServiceError(exc.reason_code, status_code=exc.status_code) from exc
    if service is None:  # pragma: no cover - required=True always raises instead.
        raise SemanticContractServiceError("capability_service_unavailable", status_code=503)
    grant_id = str(request.headers.get(_CAPABILITY_GRANT_HEADER) or "").strip()
    if not grant_id:
        raise SemanticContractServiceError("capability_grant_required", status_code=403)
    session_id = _identifier(body.get("session_id"), "session_id")
    room_id = _optional_identifier(body.get("room_id"), "room_id")
    try:
        service.require_grant_id(
            _identifier(grant_id, "grant_id"),
            capability=capability,
            tenant_id=principal.tenant_id,
            subject_id=principal.subject,
            scope_kind="room" if room_id is not None else "session",
            scope_id=room_id or session_id,
            direction=direction,
            data_type=_SEMANTIC_CONTROL_DATA_TYPE,
            purpose=_SEMANTIC_CONTROL_PURPOSE,
            epoch=_bounded_int(body.get("epoch"), "epoch", 1, 2_147_483_647),
        )
    except SemanticMediaPermissionError as exc:
        raise SemanticContractServiceError(exc.reason_code, status_code=exc.status_code) from exc


def _capability_record(
    grant: Any,
    *,
    revoked_at: float | None,
    revoked_by: str | None,
    revocation_version: int,
) -> dict[str, Any]:
    payload = asdict(grant)
    payload.update(
        {
            "revoked_at": revoked_at,
            "revoked_by": revoked_by,
            "revocation_version": revocation_version,
        }
    )
    return payload


def _hub_security_confirmed() -> bool:
    return current_app.config.get("SEMANTIC_COMPUTE_SECURITY_CONFIRMED") is True


def _require_hub_compute_enabled() -> None:
    flags = dict(current_app.extensions.get("semantic_media_feature_flags") or {})
    if not bool(flags.get("semantic_visual_capture") or flags.get("semantic_speech_runtime")):
        raise SemanticContractServiceError("feature_disabled", status_code=409)
    if not _hub_security_confirmed():
        raise SemanticContractServiceError("security_unconfirmed", status_code=409)


def _hub_fallback_healthy() -> bool:
    return current_app.config.get("SEMANTIC_COMPUTE_FALLBACK_HEALTHY", True) is True


__all__ = ["SemanticContractRouteAuthority"]
