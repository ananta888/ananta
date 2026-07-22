"""Hub composition for the additive SFU broadcast fanout subsystem.

This module is the only production wiring boundary for the broadcast-specific
ports.  Domain services remain Flask-independent.  Missing runtime authority
or an unverified adapter is represented explicitly and never replaced by the
deterministic test adapter or a permissive legacy fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from flask import Flask

from agent.repositories.sfu_broadcast_group_key_repository import (
    SqlSfuBroadcastGroupKeyRepository,
)
from agent.repositories.sfu_broadcast_repository import (
    SqlSfuAtomicGroupProjectionRepository,
    SqlSfuFanoutRouteRepository,
)
from agent.repositories.sfu_browser_capability_repository import (
    SqlSfuBrowserCapabilityRepository,
)
from agent.repositories.sfu_layer_projection_repository import (
    SqlSfuLayerProjectionRepository,
)
from agent.services.sfu_broadcast_capacity_profile_resolver import (
    ResolvedSfuBroadcastCapacityProfile,
    get_sfu_broadcast_capacity_profile_resolver,
)
from agent.services.sfu_broadcast_command_service import SfuBroadcastCommandService
from agent.services.sfu_broadcast_operations_read_model import (
    SfuBroadcastOperationsReadModel,
)
from agent.services.sfu_broadcast_route_port import (
    ROUTE_PORT_CONTRACT_V1,
    ApplyRoutePortV1,
    ObserveRoutePortV1,
    RevokeRoutePortV1,
    UpdateRoutePortV1,
)
from agent.services.sfu_browser_capability_ingestion_service import (
    SfuBrowserCapabilityIngestionService,
)
from agent.services.sfu_fanout_reconciliation_service import (
    SfuFanoutReconciliationConfig,
    SfuFanoutRouteReconciliationService,
)
from agent.services.sfu_fanout_traffic_projection import (
    SfuFanoutTrafficProjectionService,
    load_sfu_fanout_traffic_projection_policy,
)
from agent.services.sfu_group_projection_service import SfuGroupProjectionService
from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope
from agent.services.sfu_layer_projection_service import (
    SfuLayerProjectionService,
)
from agent.services.sfu_projection_signing import (
    EncodedSecretSfuProjectionPrivateKeySource,
    Ed25519SfuProjectionSigner,
    FileSfuProjectionPrivateKeySource,
    HmacSfuProjectionSigner,
    KmsEd25519SfuProjectionSigner,
    SfuProjectionSignerPort,
    SfuProjectionSigningConfigurationError,
    SfuProjectionTrustedKeyset,
)


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TRAFFIC_POLICY = _ROOT / "config" / "sfu_fanout_traffic_projection.json"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class SfuBroadcastWiringStatus:
    ready: bool
    reason_code: str | None = None

    def public(self) -> dict[str, object]:
        return {"ready": self.ready, "reason_code": self.reason_code}


@dataclass(frozen=True, slots=True)
class SfuBroadcastRouteAdapterAttestation:
    """Startup evidence that the selected adapter passed the v1 port suite."""

    adapter_id: str
    contract_version: str
    contract_suite_digest: str
    passed: bool

    def __post_init__(self) -> None:
        if (
            not self.adapter_id
            or self.contract_version != ROUTE_PORT_CONTRACT_V1
            or _DIGEST.fullmatch(self.contract_suite_digest) is None
        ):
            raise ValueError("sfu_route_adapter_attestation_invalid")


@dataclass(frozen=True, slots=True)
class SfuBroadcastRoutePorts:
    apply: ApplyRoutePortV1
    update: UpdateRoutePortV1
    revoke: RevokeRoutePortV1
    observe: ObserveRoutePortV1


@dataclass(frozen=True, slots=True)
class SfuBroadcastHubComposition:
    capacity_profile: ResolvedSfuBroadcastCapacityProfile
    traffic_projection: SfuFanoutTrafficProjectionService
    route_ports: SfuBroadcastRoutePorts | None
    statuses: Mapping[str, SfuBroadcastWiringStatus]


class _SystemRouteReconciliationClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)


class SfuBroadcastRetentionJob:
    """One lease-bounded cleanup pass over related Hub-owned state stores."""

    def __init__(
        self,
        *,
        audience_job,
        group_keys,
        browser_capabilities,
        layer_projections,
        vendor_identities=None,
        clock=time.time,
    ) -> None:
        self._audience_job = audience_job
        self._group_keys = group_keys
        self._browser_capabilities = browser_capabilities
        self._layer_projections = layer_projections
        self._vendor_identities = vendor_identities
        self._clock = clock

    def run(self, context) -> str | None:
        context.require_lease()
        cursor = self._audience_job.run(context)
        context.require_lease()
        limit = context.batch_size_max
        now = float(self._clock())
        now_ms = int(now * 1000)
        self._group_keys.purge_expired(now_ms=now_ms, limit=limit)
        context.require_lease()
        self._group_keys.rotate_envelopes(limit=limit)
        self._browser_capabilities.purge(now_ms=now_ms, limit=limit)
        self._layer_projections.purge(now_ms=now_ms, limit=limit)
        if self._vendor_identities is not None:
            self._vendor_identities.purge_expired(now=now, limit=limit)
        context.require_lease()
        return cursor


def initialize_sfu_broadcast_hub_composition(
    app: Flask,
) -> SfuBroadcastHubComposition:
    """Build the additive Hub composition once and publish narrow extensions."""

    existing = app.extensions.get("sfu_broadcast_hub_composition")
    if isinstance(existing, SfuBroadcastHubComposition):
        return existing

    statuses: dict[str, SfuBroadcastWiringStatus] = {}
    capacity_resolver = app.extensions.get("sfu_broadcast_capacity_profile_resolver")
    if capacity_resolver is None:
        capacity_resolver = get_sfu_broadcast_capacity_profile_resolver()
        app.extensions["sfu_broadcast_capacity_profile_resolver"] = capacity_resolver
    capacity_profile = capacity_resolver.resolve()
    app.extensions["sfu_broadcast_capacity_profile"] = capacity_profile
    statuses["capacity_profile"] = SfuBroadcastWiringStatus(True)

    traffic_projection = app.extensions.get("sfu_fanout_traffic_projection_service")
    if not isinstance(traffic_projection, SfuFanoutTrafficProjectionService):
        policy_path = os.environ.get(
            "ANANTA_SFU_TRAFFIC_PROJECTION_CONFIG",
            str(_DEFAULT_TRAFFIC_POLICY),
        )
        traffic_projection = SfuFanoutTrafficProjectionService(
            load_sfu_fanout_traffic_projection_policy(policy_path)
        )
        app.extensions["sfu_fanout_traffic_projection_service"] = traffic_projection
    statuses["traffic_projection"] = SfuBroadcastWiringStatus(True)

    group_key_repository = app.extensions.get("sfu_broadcast_group_key_repository")
    if group_key_repository is None:
        group_key_repository = SqlSfuBroadcastGroupKeyRepository(
            derive_sfu_hub_envelope(
                str(app.secret_key or ""),
                key_id="sfu-group-key-v1",
            )
        )
        app.extensions["sfu_broadcast_group_key_repository"] = group_key_repository
    statuses["group_key_state_receipts"] = SfuBroadcastWiringStatus(True)

    group_projection_repository = app.extensions.get("sfu_group_projection_repository")
    if group_projection_repository is None:
        group_projection_repository = SqlSfuAtomicGroupProjectionRepository()
        app.extensions["sfu_group_projection_repository"] = group_projection_repository
    group_projection_service = app.extensions.get("sfu_group_projection_service")
    if not isinstance(group_projection_service, SfuGroupProjectionService):
        group_projection_service = SfuGroupProjectionService(
            repository=group_projection_repository
        )
        app.extensions["sfu_group_projection_service"] = group_projection_service

    fanout_route_repository = app.extensions.get("sfu_fanout_route_repository")
    if fanout_route_repository is None:
        fanout_route_repository = SqlSfuFanoutRouteRepository()
        app.extensions["sfu_fanout_route_repository"] = fanout_route_repository
    statuses["group_route_state"] = SfuBroadcastWiringStatus(True)

    browser_repository = app.extensions.get("sfu_browser_capability_repository")
    if browser_repository is None:
        browser_repository = SqlSfuBrowserCapabilityRepository()
        app.extensions["sfu_browser_capability_repository"] = browser_repository
    browser_service = app.extensions.get("sfu_browser_capability_ingestion_service")
    if not isinstance(browser_service, SfuBrowserCapabilityIngestionService):
        browser_service = SfuBrowserCapabilityIngestionService(browser_repository)
        app.extensions["sfu_browser_capability_ingestion_service"] = browser_service
    browser_authority_ready = callable(
        getattr(app.extensions.get("sfu_capability_admission_scope"), "resolve", None)
    )
    statuses["browser_capability_scope"] = SfuBroadcastWiringStatus(
        browser_authority_ready,
        None if browser_authority_ready else "sfu_capability_scope_authority_unavailable",
    )

    layer_repository = app.extensions.get("sfu_layer_projection_repository")
    if layer_repository is None:
        layer_repository = SqlSfuLayerProjectionRepository()
        app.extensions["sfu_layer_projection_repository"] = layer_repository
    layer_service = app.extensions.get("sfu_layer_projection_service")
    if not isinstance(layer_service, SfuLayerProjectionService):
        signer, signing_status = _resolve_projection_signer(app)
        if signer is not None:
            layer_service = SfuLayerProjectionService(layer_repository, signer)
            app.extensions["sfu_layer_projection_service"] = layer_service
    else:
        signing_status = SfuBroadcastWiringStatus(True)
    statuses["layer_projection_signing"] = signing_status
    layer_authority_ready = callable(
        getattr(app.extensions.get("sfu_layer_projection_scope_authorizer"), "authorize", None)
    )
    statuses["layer_projection_scope"] = SfuBroadcastWiringStatus(
        layer_authority_ready,
        None if layer_authority_ready else "sfu_projection_scope_authority_unavailable",
    )
    statuses["layer_projection_state_receipts"] = SfuBroadcastWiringStatus(True)

    route_ports, route_status = _wire_route_ports(app)
    statuses["route_adapter"] = route_status
    statuses["route_reconciler"] = _wire_route_reconciler(app, route_ports)
    statuses["command_api"] = _wire_command_service(app)
    statuses["operations_read_model"] = _wire_operations_read_model(app)

    audience_job = app.extensions.get("sfu_audience_retention_job")
    if callable(getattr(audience_job, "run", None)):
        retention_job = SfuBroadcastRetentionJob(
            audience_job=audience_job,
            group_keys=group_key_repository,
            browser_capabilities=browser_repository,
            layer_projections=layer_repository,
            vendor_identities=app.extensions.get("sfu_vendor_identity_repository"),
        )
        app.extensions["sfu_broadcast_retention_job"] = retention_job
        app.extensions["sfu_audience_retention_job"] = retention_job
        statuses["retention"] = SfuBroadcastWiringStatus(True)
    else:
        statuses["retention"] = SfuBroadcastWiringStatus(
            False,
            "sfu_audience_retention_authority_unavailable",
        )

    identity_ready = all(
        app.extensions.get(name) is not None
        for name in (
            "sfu_runtime_identity_repository",
            "sfu_node_identity_service",
            "sfu_vendor_identity_repository",
            "sfu_vendor_identity_service",
        )
    )
    statuses["identity"] = SfuBroadcastWiringStatus(
        identity_ready,
        None if identity_ready else "sfu_identity_composition_unavailable",
    )

    composition = SfuBroadcastHubComposition(
        capacity_profile=capacity_profile,
        traffic_projection=traffic_projection,
        route_ports=route_ports,
        statuses=MappingProxyType(dict(statuses)),
    )
    app.extensions["sfu_broadcast_hub_composition"] = composition
    app.extensions["sfu_broadcast_hub_composition_status"] = {
        name: status.public() for name, status in statuses.items()
    }
    return composition


def _resolve_projection_signer(
    app: Flask,
) -> tuple[SfuProjectionSignerPort | None, SfuBroadcastWiringStatus]:
    mode = str(os.environ.get("ANANTA_SFU_PROJECTION_SIGNING_MODE") or "ed25519").strip().lower()
    if mode == "legacy-hmac":
        if not app.testing or not _environment_true("ANANTA_SFU_PROJECTION_ALLOW_LEGACY_HMAC"):
            return None, SfuBroadcastWiringStatus(False, "sfu_projection_legacy_hmac_forbidden")
        secret = _derived_secret(app, b"ananta:sfu-layer-projection:legacy-test:v1\x00")
        signer = HmacSfuProjectionSigner(
            secret,
            key_id="legacy-hmac-test:v1",
            key_version=1,
            legacy_mode=True,
        )
        app.extensions["sfu_projection_signer"] = signer
        return signer, SfuBroadcastWiringStatus(True, "sfu_projection_legacy_hmac_test_only")
    if mode != "ed25519":
        return None, SfuBroadcastWiringStatus(False, "sfu_projection_signing_mode_invalid")

    key_id = str(os.environ.get("ANANTA_SFU_PROJECTION_SIGNING_KEY_ID") or "").strip()
    try:
        key_version = int(os.environ.get("ANANTA_SFU_PROJECTION_SIGNING_KEY_VERSION") or "0")
    except ValueError:
        key_version = 0
    secret = str(os.environ.get("ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_B64URL") or "").strip()
    key_file = str(os.environ.get("ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_FILE") or "").strip()
    kms = app.extensions.get("sfu_projection_ed25519_kms_adapter")
    configured_sources = int(bool(secret)) + int(bool(key_file)) + int(kms is not None)
    if configured_sources != 1:
        return None, SfuBroadcastWiringStatus(False, "sfu_projection_private_key_unavailable")
    try:
        if kms is not None:
            signer: SfuProjectionSignerPort = KmsEd25519SfuProjectionSigner(
                kms,
                key_id=key_id,
                key_version=key_version,
            )
        else:
            source = (
                EncodedSecretSfuProjectionPrivateKeySource(secret)
                if secret
                else FileSfuProjectionPrivateKeySource(key_file)
            )
            signer = Ed25519SfuProjectionSigner(
                source,
                key_id=key_id,
                key_version=key_version,
            )
        keyset_json = str(os.environ.get("ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_JSON") or "").strip()
        keyset_file = str(os.environ.get("ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_FILE") or "").strip()
        if int(bool(keyset_json)) + int(bool(keyset_file)) != 1:
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_unavailable")
        keyset = (
            SfuProjectionTrustedKeyset.from_json(keyset_json)
            if keyset_json
            else SfuProjectionTrustedKeyset.from_file(keyset_file)
        )
        if not keyset.authorizes(signer):
            raise SfuProjectionSigningConfigurationError("sfu_projection_active_key_not_pinned")
    except (OSError, ValueError, json.JSONDecodeError, SfuProjectionSigningConfigurationError):
        return None, SfuBroadcastWiringStatus(False, "sfu_projection_signing_configuration_invalid")
    app.extensions["sfu_projection_signer"] = signer
    app.extensions["sfu_projection_trusted_keyset_bootstrap"] = keyset.public()
    return signer, SfuBroadcastWiringStatus(True)


def _environment_true(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _wire_route_ports(
    app: Flask,
) -> tuple[SfuBroadcastRoutePorts | None, SfuBroadcastWiringStatus]:
    candidate = app.extensions.get("sfu_broadcast_route_adapter")
    apply_port = app.extensions.get("sfu_broadcast_apply_route_port") or candidate
    update_port = app.extensions.get("sfu_broadcast_update_route_port") or candidate
    revoke_port = app.extensions.get("sfu_broadcast_revoke_route_port") or candidate
    observe_port = app.extensions.get("sfu_broadcast_observe_route_port") or candidate
    ports = (apply_port, update_port, revoke_port, observe_port)
    if not all(ports):
        return None, SfuBroadcastWiringStatus(False, "sfu_route_adapter_unavailable")
    if not (
        isinstance(apply_port, ApplyRoutePortV1)
        and isinstance(update_port, UpdateRoutePortV1)
        and isinstance(revoke_port, RevokeRoutePortV1)
        and isinstance(observe_port, ObserveRoutePortV1)
    ):
        return None, SfuBroadcastWiringStatus(False, "sfu_route_adapter_contract_invalid")
    attestation = app.extensions.get("sfu_broadcast_route_adapter_attestation")
    if not isinstance(attestation, SfuBroadcastRouteAdapterAttestation) or attestation.passed is not True:
        return None, SfuBroadcastWiringStatus(False, "sfu_route_adapter_contract_unverified")
    resolved = SfuBroadcastRoutePorts(apply_port, update_port, revoke_port, observe_port)
    app.extensions["sfu_broadcast_apply_route_port"] = resolved.apply
    app.extensions["sfu_broadcast_update_route_port"] = resolved.update
    app.extensions["sfu_broadcast_revoke_route_port"] = resolved.revoke
    app.extensions["sfu_broadcast_observe_route_port"] = resolved.observe
    return resolved, SfuBroadcastWiringStatus(True)


def _wire_route_reconciler(
    app: Flask,
    route_ports: SfuBroadcastRoutePorts | None,
) -> SfuBroadcastWiringStatus:
    if route_ports is None:
        return SfuBroadcastWiringStatus(False, "sfu_route_adapter_unavailable")
    dependencies = {
        "leases": app.extensions.get("sfu_fanout_route_reconciliation_lease_port"),
        "pages": app.extensions.get("sfu_fanout_route_reconciliation_page_port"),
        "authority": app.extensions.get("sfu_fanout_route_reconciliation_authority_port"),
        "checkpoints": app.extensions.get("sfu_fanout_route_reconciliation_checkpoint_port"),
        "outcomes": app.extensions.get("sfu_fanout_route_reconciliation_outcome_port"),
    }
    if any(value is None for value in dependencies.values()):
        return SfuBroadcastWiringStatus(
            False,
            "sfu_route_reconciliation_state_ports_unavailable",
        )
    service = SfuFanoutRouteReconciliationService(
        config=SfuFanoutReconciliationConfig(),
        clock=_SystemRouteReconciliationClock(),
        leases=dependencies["leases"],
        pages=dependencies["pages"],
        authority=dependencies["authority"],
        checkpoints=dependencies["checkpoints"],
        outcomes=dependencies["outcomes"],
        apply_routes=route_ports.apply,
        update_routes=route_ports.update,
        revoke_routes=route_ports.revoke,
        observe_routes=route_ports.observe,
    )
    app.extensions["sfu_fanout_route_reconciliation_service"] = service
    if not callable(getattr(app.extensions.get("sfu_fanout_route_reconciler_job"), "run", None)):
        return SfuBroadcastWiringStatus(
            False,
            "sfu_route_reconciliation_scope_driver_unavailable",
        )
    return SfuBroadcastWiringStatus(True)


def _wire_command_service(app: Flask) -> SfuBroadcastWiringStatus:
    existing = app.extensions.get("sfu_broadcast_command_service")
    if isinstance(existing, SfuBroadcastCommandService):
        return SfuBroadcastWiringStatus(True)
    authorizer = app.extensions.get("sfu_broadcast_command_authorization_port")
    executor = app.extensions.get("sfu_broadcast_command_executor_port")
    ledger = app.extensions.get("sfu_broadcast_command_ledger")
    if authorizer is None or executor is None or ledger is None:
        return SfuBroadcastWiringStatus(False, "sfu_command_durable_ports_unavailable")
    app.extensions["sfu_broadcast_command_service"] = SfuBroadcastCommandService(
        authorizer=authorizer,
        executor=executor,
        ledger=ledger,
        diagnostic_secret=_derived_secret(app, b"ananta:sfu-command:v1\x00"),
    )
    return SfuBroadcastWiringStatus(True)


def _wire_operations_read_model(app: Flask) -> SfuBroadcastWiringStatus:
    existing = app.extensions.get("sfu_broadcast_operations_read_model")
    if isinstance(existing, SfuBroadcastOperationsReadModel):
        return SfuBroadcastWiringStatus(True)
    source = app.extensions.get("sfu_broadcast_operations_snapshot_port")
    if source is None:
        return SfuBroadcastWiringStatus(False, "sfu_operations_snapshot_port_unavailable")
    app.extensions["sfu_broadcast_operations_read_model"] = SfuBroadcastOperationsReadModel(
        source=source,
        diagnostic_secret=_derived_secret(app, b"ananta:sfu-operations:v1\x00"),
    )
    return SfuBroadcastWiringStatus(True)


def _derived_secret(app: Flask, domain: bytes) -> bytes:
    return hashlib.sha256(domain + str(app.secret_key or "").encode("utf-8")).digest()


__all__ = [
    "SfuBroadcastHubComposition",
    "SfuBroadcastRetentionJob",
    "SfuBroadcastRouteAdapterAttestation",
    "SfuBroadcastRoutePorts",
    "SfuBroadcastWiringStatus",
    "initialize_sfu_broadcast_hub_composition",
]
