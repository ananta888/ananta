"""Explicit TURN consumption paths for peers and SFU infrastructure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from agent.services.turn_pool_directory import (
    TurnPoolDirectory,
    TurnPoolDirectoryError,
    TurnPoolSelectionQuery,
)


class TurnIntegrationError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


class TurnConsumptionPath(StrEnum):
    DIRECT = "direct"
    PEER_TURN = "peer_turn"
    LIVEKIT_SFU = "livekit_sfu"
    SFU_ALL_TURN = "sfu_all_turn"


@dataclass(frozen=True)
class TurnCapabilityEvidence:
    capability: str
    source_refs: tuple[str, ...]
    approved: bool


@dataclass(frozen=True)
class TurnIntegrationRequest:
    path: TurnConsumptionPath
    pool_id: str
    region: str
    transport: str
    receiver_stability_ref: str
    config_version: str
    trust_policy_version: str
    credential_ttl_seconds: int
    excluded_instance_ids: tuple[str, ...] = ()
    retry_index: int = 0


@dataclass(frozen=True)
class TurnIntegrationResult:
    path: TurnConsumptionPath
    ice_servers: tuple[Mapping[str, Any], ...]
    selected_instance_id: str | None
    reason_code: str
    evidence_refs: tuple[str, ...]


EndpointAuthorizer = Callable[[str, str, str], str]
CredentialIssuer = Callable[[str, int], Mapping[str, str]]


class WebrtcTurnIntegrationService:
    """Composes directory, endpoint policy and short-lived credentials.

    Peer TURN support is not evidence that LiveKit itself supports external
    TURN, nor that SFU-to-peer media can be forced through TURN.  Those two
    paths remain unavailable until independently grounded capability evidence
    is supplied by the hub.
    """

    _SOURCE_REF = re.compile(r"^(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    _EVIDENCE_CAPABILITIES = {
        TurnConsumptionPath.LIVEKIT_SFU: ("livekit_external_turn",),
        TurnConsumptionPath.SFU_ALL_TURN: ("livekit_external_turn", "sfu_all_turn_media_path"),
    }

    def __init__(
        self,
        directory: TurnPoolDirectory,
        *,
        endpoint_authorizer: EndpointAuthorizer,
        credential_issuer: CredentialIssuer,
        capability_evidence: tuple[TurnCapabilityEvidence, ...] = (),
    ) -> None:
        self._directory = directory
        self._endpoint_authorizer = endpoint_authorizer
        self._credential_issuer = credential_issuer
        self._evidence = {item.capability: item for item in capability_evidence}

    def resolve(self, request: TurnIntegrationRequest) -> TurnIntegrationResult:
        if request.path == TurnConsumptionPath.DIRECT:
            return TurnIntegrationResult(
                path=request.path,
                ice_servers=(),
                selected_instance_id=None,
                reason_code="turn_not_requested",
                evidence_refs=(),
            )
        evidence_refs = self._require_path_evidence(request.path)
        consumer = {
            TurnConsumptionPath.PEER_TURN: "peer",
            TurnConsumptionPath.LIVEKIT_SFU: "livekit_sfu",
            TurnConsumptionPath.SFU_ALL_TURN: "sfu_all_turn",
        }[request.path]
        try:
            selected = self._directory.select(
                TurnPoolSelectionQuery(
                    pool_id=request.pool_id,
                    region=request.region,
                    consumer=consumer,
                    transport=request.transport,
                    credential_mode="rest_hmac_sha256",
                    config_version=request.config_version,
                    trust_policy_version=request.trust_policy_version,
                    receiver_stability_ref=request.receiver_stability_ref,
                    excluded_instance_ids=request.excluded_instance_ids,
                    retry_index=request.retry_index,
                )
            )
        except TurnPoolDirectoryError as exc:
            raise TurnIntegrationError(exc.reason_code, exc.status_code) from exc
        urls = tuple(
            self._endpoint_authorizer(endpoint["url"], consumer, request.transport)
            for endpoint in selected.endpoints
        )
        if not urls:
            raise TurnIntegrationError("turn_endpoint_policy_rejected", 503)
        credential = dict(self._credential_issuer(request.receiver_stability_ref, request.credential_ttl_seconds))
        if set(credential) != {"username", "credential"} or not all(credential.values()):
            raise TurnIntegrationError("turn_credential_contract_invalid", 503)
        return TurnIntegrationResult(
            path=request.path,
            ice_servers=(
                {
                    "urls": list(urls),
                    "username": credential["username"],
                    "credential": credential["credential"],
                    "credentialType": "password",
                },
            ),
            selected_instance_id=selected.instance_id,
            reason_code="turn_path_selected",
            evidence_refs=evidence_refs,
        )

    def _require_path_evidence(self, path: TurnConsumptionPath) -> tuple[str, ...]:
        capabilities = self._EVIDENCE_CAPABILITIES.get(path)
        if capabilities is None:
            return ()
        refs: list[str] = []
        for capability in capabilities:
            evidence = self._evidence.get(capability)
            if (
                evidence is None
                or not evidence.approved
                or not evidence.source_refs
                or any(not self._SOURCE_REF.fullmatch(value) for value in evidence.source_refs)
            ):
                raise TurnIntegrationError("turn_path_capability_unverified", 503)
            refs.extend(evidence.source_refs)
        return tuple(dict.fromkeys(refs))
