from __future__ import annotations

import base64
import importlib
import json
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
from flask import Flask
from sqlalchemy import func, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from agent.db_models import (
    SfuNodeDB,
    SfuNodeMutationDB,
    SfuNodeObservationCursorDB,
    SfuNodeObservationReplayDB,
)
from agent.repositories.sfu_node_observation_cursor_repository import (
    SqlSfuNodeObservationCursorRepository,
)
from agent.repositories.sfu_node_repository import SqlSfuNodeRepository
from agent.routes.webrtc_sfu_node_observations import webrtc_sfu_node_observations_bp
from agent.services.sfu_node_identity_service import (
    AUTHENTICATED_RUNTIME_EXTENSION,
    LIVEKIT_CONTROL_API,
    SfuNodeIdentityError,
)
from agent.services.sfu_node_observation_ingestion_service import (
    SfuNodeObservationAuthentication,
    SfuNodeObservationError,
    SfuNodeObservationIngestionService,
    SfuNodeObservationPolicy,
    build_sfu_node_observation_signature_message,
    build_sfu_node_observation_validator,
    collector_token_digest,
)


NOW = 1_800_000_000.0
CURSOR_KEY = b"test-observation-node-list-cursor"
COLLECTOR_ID = "hub-livekit-collector-a"
COLLECTOR_TOKEN = "collector-token-with-enough-entropy"


class _IdentityAuthorizer:
    def __init__(self, *, identity_id="runtime-node-a", reject=None):
        self.identity_id = identity_id
        self.reject = reject
        self.calls = []

    def authorize_extension_peer(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject:
            raise SfuNodeIdentityError(self.reject, status_code=403)
        return SimpleNamespace(id=self.identity_id, node_id=kwargs["node_id"])


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SfuNodeDB.__table__,
            SfuNodeMutationDB.__table__,
            SfuNodeObservationCursorDB.__table__,
            SfuNodeObservationReplayDB.__table__,
        ],
    )
    return engine


def _service(
    *,
    engine=None,
    mode=LIVEKIT_CONTROL_API,
    clock=lambda: NOW,
    identity=None,
    entries_max=8,
    sequence_window=4,
):
    engine = engine or _engine()
    node_repository = SqlSfuNodeRepository(
        db_engine=engine,
        clock=clock,
        cursor_signing_key=CURSOR_KEY,
    )
    node_repository.enroll_node(
        tenant_id="tenant-a",
        cluster_id="cluster-a",
        node_id="node-a",
        runtime_identity_id="runtime-node-a",
        region="eu-central",
        adapter_name="livekit",
        adapter_version="1.9.0",
        protocol_version="1",
        capability_digest="sha256:" + "1" * 64,
        expected_version=0,
        fencing_token=7,
    )
    policy = SfuNodeObservationPolicy(
        runtime_control_mode=mode,
        collector_id=COLLECTOR_ID if mode == LIVEKIT_CONTROL_API else "",
        cursor_entries_max=entries_max,
        sequence_window=sequence_window,
        hard_limits={
            "cpu_percent": 90.0,
            "memory_bytes": 1000,
            "fd_count": 100,
            "ingress_bps": 1000,
            "egress_bps": 1000,
            "turn_ratio": 1.0,
            "rooms": 10,
            "tracks": 100,
            "receivers": 1000,
        },
    )
    service = SfuNodeObservationIngestionService(
        cursor_repository=SqlSfuNodeObservationCursorRepository(
            db_engine=engine,
            clock=clock,
        ),
        node_repository=node_repository,
        identity_service=identity or _IdentityAuthorizer(),
        policy=policy,
        validator=build_sfu_node_observation_validator(clock=clock),
        clock=clock,
    )
    return service, engine, node_repository


def _document(
    *,
    mode=LIVEKIT_CONTROL_API,
    producer_id=COLLECTOR_ID,
    boot_id="boot-id-0001",
    sequence=1,
    measured_at=NOW,
    node_version=1,
    node_id="node-a",
):
    return {
        "schema_version": 1,
        "producer_mode": mode,
        "tenant_id": "tenant-a",
        "cluster_id": "cluster-a",
        "region": "eu-central",
        "node_id": node_id,
        "producer_id": producer_id,
        "boot_id": boot_id,
        "sequence": sequence,
        "measured_at": measured_at,
        "ttl_seconds": 20,
        "adapter_name": "livekit",
        "adapter_version": "1.9.1",
        "protocol_version": "1",
        "capability_digest": "sha256:" + "a" * 64,
        "health_status": "healthy",
        "drain_state": "active",
        "fencing_token": 7 if node_id is not None else 0,
        "node_version": node_version if node_id is not None else 0,
        "metrics": {
            "cpu_percent": 95.0,
            "memory_bytes": 2000,
            "fd_count": 50,
            "ingress_bps": 500,
            "egress_bps": 500,
            "turn_ratio": 0.25,
            "rooms": 2,
            "tracks": 20,
            "receivers": 30,
        },
        "labels": {"source": "bounded-test"},
    }


def _raw(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _control_auth(*, tls=True, authenticated=True):
    return SfuNodeObservationAuthentication(
        transport_tls_verified=tls,
        collector_authenticated=authenticated,
    )


def _certificate():
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "node-a")])
    now = datetime.fromtimestamp(NOW, timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, algorithm=None)
    )
    return key, certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _signed_extension_document(key, **overrides):
    document = _document(
        mode=AUTHENTICATED_RUNTIME_EXTENSION,
        producer_id="runtime-node-a",
        **overrides,
    )
    signature = key.sign(build_sfu_node_observation_signature_message(document))
    document["signature"] = {
        "algorithm": "Ed25519",
        "value": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }
    return document


def test_migration_is_on_ops003_head_and_round_trips():
    migration = importlib.import_module(
        "migrations.versions.2ae1f2a3b4c5_add_sfu_node_observation_cursors"
    )
    assert migration.down_revision == "19d0e1f2a3b4"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert {
            "sfu_node_observation_cursors",
            "sfu_node_observation_replays",
        }.issubset(inspect(connection).get_table_names())
        migration.downgrade()
        assert "sfu_node_observation_cursors" not in inspect(connection).get_table_names()


def test_control_api_collector_clamps_non_authoritative_values_without_mutating_drain():
    service, _, node_repository = _service()
    result = service.ingest(_raw(_document()), _control_auth())
    assert result.status == "accepted"
    assert result.normalized_observation["authoritative"] is False
    assert result.normalized_observation["validity"] == "clamped"
    assert result.normalized_observation["clamped_fields"] == [
        "cpu_percent",
        "memory_bytes",
    ]
    assert result.normalized_observation["metrics"]["memory_bytes"] == 1000
    node = node_repository.get_node(
        tenant_id="tenant-a", cluster_id="cluster-a", node_id="node-a"
    )
    assert node is not None
    assert node.health_status == "healthy"
    assert node.drain_state == "active"
    assert node.fencing_token == 7


@pytest.mark.parametrize(
    ("authentication", "mutation", "expected"),
    [
        (_control_auth(tls=False), {}, "sfu_observation_tls_required"),
        (_control_auth(authenticated=False), {}, "sfu_observation_collector_unauthorized"),
        (_control_auth(), {"producer_id": "forged"}, "sfu_observation_collector_identity_mismatch"),
        (
            _control_auth(),
            {"signature": {"algorithm": "Ed25519", "value": "A" * 86}},
            "sfu_observation_unexpected_node_signature",
        ),
    ],
)
def test_control_api_rejects_wrong_tls_auth_identity_and_invented_node_signature(
    authentication, mutation, expected
):
    service, _, _ = _service()
    document = _document()
    document.update(mutation)
    with pytest.raises(SfuNodeObservationError, match=expected):
        service.ingest(_raw(document), authentication)


def test_extension_requires_bound_mtls_identity_and_valid_payload_signature():
    identity = _IdentityAuthorizer()
    service, _, _ = _service(
        mode=AUTHENTICATED_RUNTIME_EXTENSION,
        identity=identity,
    )
    key, certificate = _certificate()
    document = _signed_extension_document(key)
    authentication = SfuNodeObservationAuthentication(
        transport_tls_verified=True,
        peer_certificate_pem=certificate,
    )
    assert service.ingest(_raw(document), authentication).status == "accepted"
    assert identity.calls[0]["required_role"] == "sfu_observer"

    forged = _signed_extension_document(ed25519.Ed25519PrivateKey.generate(), sequence=2, node_version=2)
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_payload_signature_invalid"):
        service.ingest(_raw(forged), authentication)
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_mtls_identity_required"):
        service.ingest(
            _raw(_signed_extension_document(key, sequence=2, node_version=2)),
            SfuNodeObservationAuthentication(transport_tls_verified=True),
        )


def test_duplicate_restart_reorder_window_and_stale_are_persistent_and_reason_coded():
    engine = _engine()
    first_hub, _, _ = _service(engine=engine)
    initial = _document(sequence=10)
    assert first_hub.ingest(_raw(initial), _control_auth()).status == "accepted"

    restarted = SfuNodeObservationIngestionService(
        cursor_repository=SqlSfuNodeObservationCursorRepository(db_engine=engine, clock=lambda: NOW),
        node_repository=SqlSfuNodeRepository(
            db_engine=engine,
            clock=lambda: NOW,
            cursor_signing_key=CURSOR_KEY,
        ),
        identity_service=_IdentityAuthorizer(),
        policy=SfuNodeObservationPolicy(
            runtime_control_mode=LIVEKIT_CONTROL_API,
            collector_id=COLLECTOR_ID,
            sequence_window=4,
            cursor_entries_max=8,
        ),
        validator=build_sfu_node_observation_validator(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    assert restarted.ingest(_raw(initial), _control_auth()).status == "duplicate"
    reordered = _document(sequence=8, node_version=2)
    assert restarted.ingest(_raw(reordered), _control_auth()).status == "accepted_reordered"
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_sequence_outside_window"):
        restarted.ingest(_raw(_document(sequence=6, node_version=2)), _control_auth())
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_stale"):
        restarted.ingest(
            _raw(_document(sequence=11, node_version=2, measured_at=NOW - 21)),
            _control_auth(),
        )


def test_boot_reset_retires_old_boot_and_fences_sequence_payload_conflicts():
    service, _, _ = _service()
    assert service.ingest(_raw(_document(sequence=5)), _control_auth()).status == "accepted"
    reset = _document(boot_id="boot-id-0002", sequence=1, node_version=2)
    assert service.ingest(_raw(reset), _control_auth()).status == "accepted"
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_boot_replay"):
        service.ingest(
            _raw(_document(boot_id="boot-id-0001", sequence=6, node_version=3)),
            _control_auth(),
        )
    conflict = dict(reset)
    conflict["health_status"] = "degraded"
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_sequence_payload_conflict"):
        service.ingest(_raw(conflict), _control_auth())


def test_cluster_observation_does_not_invent_node_identity_or_mutate_directory():
    service, _, node_repository = _service()
    cluster_document = _document(node_id=None)
    result = service.ingest(_raw(cluster_document), _control_auth())
    assert result.status == "accepted"
    assert result.node is None
    node = node_repository.get_node(
        tenant_id="tenant-a", cluster_id="cluster-a", node_id="node-a"
    )
    assert node is not None
    assert node.version == 1
    assert node.observation_status == "unknown"


def test_oversize_label_flood_invalid_values_and_clock_skew_fail_before_persistence():
    service, engine, _ = _service()
    with pytest.raises(SfuNodeObservationError, match="contract_document_bytes_exceeded"):
        service.ingest(b"{" + b" " * 40_000 + b"}", _control_auth())
    labels = {f"label_{index}": "x" for index in range(65)}
    flooded = _document()
    flooded["labels"] = labels
    with pytest.raises(SfuNodeObservationError):
        service.ingest(_raw(flooded), _control_auth())
    invalid = _document()
    invalid["metrics"]["memory_bytes"] = -1
    with pytest.raises(SfuNodeObservationError, match="contract_schema_invalid"):
        service.ingest(_raw(invalid), _control_auth())
    with pytest.raises(SfuNodeObservationError, match="sfu_observation_clock_skew"):
        service.ingest(_raw(_document(measured_at=NOW + 6)), _control_auth())
    with Session(engine) as db:
        assert db.exec(select(func.count()).select_from(SfuNodeObservationReplayDB)).one() == 0


def test_deterministic_fuzz_and_ten_thousand_replays_remain_bounded():
    service, engine, _ = _service(entries_max=8, sequence_window=4)
    rng = random.Random(20260722)
    for _ in range(256):
        candidate = bytes(rng.randrange(0, 256) for _ in range(rng.randrange(0, 512)))
        try:
            service.ingest(candidate, _control_auth())
        except SfuNodeObservationError:
            pass
    raw = _raw(_document())
    assert service.ingest(raw, _control_auth()).status == "accepted"
    for _ in range(10_000):
        assert service.ingest(raw, _control_auth()).status == "duplicate"
    with Session(engine) as db:
        assert db.exec(select(func.count()).select_from(SfuNodeObservationCursorDB)).one() == 1
        assert db.exec(select(func.count()).select_from(SfuNodeObservationReplayDB)).one() == 1


def test_internal_route_requires_https_and_hub_collector_bearer():
    service, _, _ = _service()
    app = Flask(__name__)
    app.secret_key = "test"
    app.extensions["sfu_node_observation_ingestion_service"] = service
    app.extensions["sfu_node_observation_collector_token_digest"] = collector_token_digest(
        COLLECTOR_TOKEN
    )
    app.register_blueprint(webrtc_sfu_node_observations_bp)
    response = app.test_client().post(
        "/api/internal/webrtc/sfu-node-observations",
        base_url="https://ananta.test",
        headers={"Authorization": f"Bearer {COLLECTOR_TOKEN}"},
        data=_raw(_document()),
        content_type="application/json",
    )
    assert response.status_code == 202
    assert response.get_json()["data"]["status"] == "accepted"
