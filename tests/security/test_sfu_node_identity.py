from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from flask import Flask, g
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models import (
    SfuRuntimeCredentialDB,
    SfuRuntimeEnrollmentRateLimitDB,
    SfuRuntimeIdentityDB,
    SfuRuntimeIdentityMutationDB,
)
from agent.repositories.sfu_runtime_identity_repository import SqlSfuRuntimeIdentityRepository
from agent.routes.webrtc_sfu_node_enrollment import webrtc_sfu_node_enrollment_bp
from agent.services.sfu_node_identity_service import (
    AUTHENTICATED_RUNTIME_EXTENSION,
    LIVEKIT_CONTROL_API,
    SFU_CONTROL,
    SFU_OBSERVER,
    SfuNodeCredentialCommand,
    SfuNodeIdentityError,
    SfuNodeIdentityService,
    SfuNodeRevocationCommand,
    SfuNodeTrustPolicy,
    SfuProofOfPossession,
    build_sfu_node_pop_message,
    certificate_fingerprint,
    public_key_fingerprint,
)

NOW = 1_800_000_000.0


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SfuRuntimeIdentityDB.__table__,
            SfuRuntimeCredentialDB.__table__,
            SfuRuntimeIdentityMutationDB.__table__,
            SfuRuntimeEnrollmentRateLimitDB.__table__,
        ],
    )
    return engine


def _api_policy(**overrides):
    values = {
        "runtime_control_mode": LIVEKIT_CONTROL_API,
        "activation_enabled": True,
        "enrollment_max_attempts": 5,
        "rotation_overlap_seconds": 10,
        "node_revocation_max_seconds": 7,
    }
    values.update(overrides)
    return SfuNodeTrustPolicy(**values)


def _service(*, engine=None, policy=None, clock=lambda: NOW):
    engine = engine or _engine()
    return SfuNodeIdentityService(
        SqlSfuRuntimeIdentityRepository(db_engine=engine, clock=clock),
        policy or _api_policy(),
        clock=clock,
    )


def _public_pem(private_key) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _api_command(
    private_key,
    *,
    node_id="node-a",
    role=SFU_CONTROL,
    fingerprint="sha256:" + "1" * 64,
    expected_version=0,
    actor="admin-a",
    reason="initial controlled enrollment",
    idempotency_key="idem-enrollment-0001",
    issued_at=NOW,
    nonce="nonce-unique-value-0001",
    signature_key=None,
):
    public_pem = _public_pem(private_key)
    message = build_sfu_node_pop_message(
        operation="enroll" if expected_version == 0 else "rotate",
        node_id=node_id,
        runtime_control_mode=LIVEKIT_CONTROL_API,
        roles=(role,),
        public_key_fingerprint=public_key_fingerprint(public_pem),
        credential_fingerprint=fingerprint,
        nonce=nonce,
        issued_at=issued_at,
        expected_version=expected_version,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    signature = (signature_key or private_key).sign(message)
    return SfuNodeCredentialCommand(
        node_id=node_id,
        runtime_control_mode=LIVEKIT_CONTROL_API,
        roles=(role,),
        public_key_pem=public_pem,
        credential_kind="livekit_api_credential",
        credential_fingerprint=fingerprint,
        certificate_pem=None,
        proof=SfuProofOfPossession(
            algorithm="Ed25519",
            signature=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
            nonce=nonce,
            issued_at=issued_at,
        ),
        expected_version=expected_version,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )


def _ca(common_name="Ananta test CA"):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.fromtimestamp(NOW, timezone.utc) + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf(ca_key, ca_cert, key, *, node_id="node-a", role=SFU_CONTROL, start=-60, end=3600):
    now = datetime.fromtimestamp(NOW, timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(seconds=start))
        .not_valid_after(now + timedelta(seconds=end))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"spiffe://ananta.local/sfu/{role}/{node_id}")]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _extension_command(private_key, certificate_pem, *, role=SFU_CONTROL, issued_at=NOW):
    public_pem = _public_pem(private_key)
    fingerprint = certificate_fingerprint(certificate_pem)
    message = build_sfu_node_pop_message(
        operation="enroll",
        node_id="node-a",
        runtime_control_mode=AUTHENTICATED_RUNTIME_EXTENSION,
        roles=(role,),
        public_key_fingerprint=public_key_fingerprint(public_pem),
        credential_fingerprint=fingerprint,
        nonce="extension-proof-nonce-01",
        issued_at=issued_at,
        expected_version=0,
        actor="admin-a",
        reason="extension identity enrollment",
        idempotency_key="extension-idempotency-01",
    )
    signature = private_key.sign(message)
    return SfuNodeCredentialCommand(
        node_id="node-a",
        runtime_control_mode=AUTHENTICATED_RUNTIME_EXTENSION,
        roles=(role,),
        public_key_pem=public_pem,
        credential_kind="mtls_client_certificate",
        credential_fingerprint=fingerprint,
        certificate_pem=certificate_pem,
        proof=SfuProofOfPossession(
            algorithm="Ed25519",
            signature=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
            nonce="extension-proof-nonce-01",
            issued_at=issued_at,
        ),
        expected_version=0,
        actor="admin-a",
        reason="extension identity enrollment",
        idempotency_key="extension-idempotency-01",
    )


def test_enrollment_pop_idempotency_rate_limit_and_restart_are_durable():
    engine = _engine()
    key = ed25519.Ed25519PrivateKey.generate()
    command = _api_command(key)
    first_hub = _service(engine=engine)
    assert first_hub.enroll(command, source="192.0.2.1").status == "created"
    assert first_hub.enroll(command, source="192.0.2.1").status == "replayed"
    restarted_hub = _service(engine=engine)
    assert restarted_hub.get("node-a").version == 1
    assert restarted_hub.authorize_livekit_control_credential(
        node_id="node-a",
        configured_credential_fingerprint="sha256:" + "1" * 64,
        required_role=SFU_CONTROL,
        tls_verified=True,
    ).node_id == "node-a"


def test_wrong_or_stale_proof_and_reused_idempotency_fail_closed():
    service = _service()
    key = ed25519.Ed25519PrivateKey.generate()
    wrong = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(SfuNodeIdentityError, match="sfu_proof_signature_invalid"):
        service.enroll(_api_command(key, signature_key=wrong), source="192.0.2.2")
    with pytest.raises(SfuNodeIdentityError, match="sfu_proof_clock_skew"):
        service.enroll(
            _api_command(key, issued_at=NOW - 31, idempotency_key="idem-enrollment-0002"),
            source="192.0.2.2",
        )
    service.enroll(_api_command(key), source="192.0.2.2")
    changed = _api_command(
        key,
        fingerprint="sha256:" + "2" * 64,
        idempotency_key="idem-enrollment-0001",
        nonce="nonce-unique-value-0002",
    )
    with pytest.raises(SfuNodeIdentityError, match="sfu_identity_idempotency_conflict"):
        service.enroll(changed, source="192.0.2.2")


def test_persistent_rate_limit_is_shared_by_hubs():
    engine = _engine()
    policy = _api_policy(enrollment_max_attempts=1)
    key = ed25519.Ed25519PrivateKey.generate()
    wrong = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(SfuNodeIdentityError, match="sfu_proof_signature_invalid"):
        _service(engine=engine, policy=policy).enroll(
            _api_command(key, signature_key=wrong), source="198.51.100.3"
        )
    with pytest.raises(SfuNodeIdentityError, match="sfu_enrollment_rate_limited"):
        _service(engine=engine, policy=policy).enroll(
            _api_command(key, idempotency_key="idem-enrollment-0003"), source="198.51.100.3"
        )


def test_observer_cannot_authorize_control_and_tls_is_mandatory():
    service = _service()
    key = ed25519.Ed25519PrivateKey.generate()
    service.enroll(_api_command(key, role=SFU_OBSERVER), source="192.0.2.4")
    with pytest.raises(SfuNodeIdentityError, match="sfu_identity_role_forbidden"):
        service.authorize_livekit_control_credential(
            node_id="node-a",
            configured_credential_fingerprint="sha256:" + "1" * 64,
            required_role=SFU_CONTROL,
            tls_verified=True,
        )
    with pytest.raises(SfuNodeIdentityError, match="sfu_livekit_tls_unverified"):
        service.authorize_livekit_control_credential(
            node_id="node-a",
            configured_credential_fingerprint="sha256:" + "1" * 64,
            required_role=SFU_OBSERVER,
            tls_verified=False,
        )


def test_rotation_overlap_then_old_api_credential_expires():
    now = [NOW]
    service = _service(clock=lambda: now[0])
    first_key = ed25519.Ed25519PrivateKey.generate()
    service.enroll(_api_command(first_key), source="192.0.2.5")
    second_key = ed25519.Ed25519PrivateKey.generate()
    rotated = service.rotate(
        _api_command(
            second_key,
            fingerprint="sha256:" + "2" * 64,
            expected_version=1,
            idempotency_key="idem-rotation-000001",
            nonce="nonce-rotation-value-001",
            reason="scheduled credential rotation",
        )
    )
    assert rotated.identity.version == 2
    service.authorize_livekit_control_credential(
        node_id="node-a",
        configured_credential_fingerprint="sha256:" + "1" * 64,
        required_role=SFU_CONTROL,
        tls_verified=True,
    )
    now[0] += 11
    with pytest.raises(SfuNodeIdentityError, match="sfu_runtime_credential_rejected"):
        service.authorize_livekit_control_credential(
            node_id="node-a",
            configured_credential_fingerprint="sha256:" + "1" * 64,
            required_role=SFU_CONTROL,
            tls_verified=True,
        )


def test_api_credential_emergency_revoke_is_immediate_and_cas_fenced():
    service = _service()
    key = ed25519.Ed25519PrivateKey.generate()
    service.enroll(_api_command(key), source="192.0.2.6")
    revoked = service.revoke(
        SfuNodeRevocationCommand(
            node_id="node-a",
            expected_version=1,
            emergency=True,
            actor="admin-a",
            reason="suspected credential theft",
            idempotency_key="idem-emergency-revoke-1",
        )
    )
    assert revoked.identity.revocation_deadline_at == NOW + 7
    with pytest.raises(SfuNodeIdentityError, match="sfu_identity_revoked"):
        service.authorize_livekit_control_credential(
            node_id="node-a",
            configured_credential_fingerprint="sha256:" + "1" * 64,
            required_role=SFU_CONTROL,
            tls_verified=True,
        )
    with pytest.raises(SfuNodeIdentityError, match="sfu_identity_version_conflict"):
        service.revoke(
            SfuNodeRevocationCommand(
                node_id="node-a",
                expected_version=1,
                emergency=True,
                actor="admin-a",
                reason="stale competing revoke",
                idempotency_key="idem-emergency-revoke-2",
            )
        )


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("foreign", "sfu_certificate_foreign_ca"),
        ("wrong_san", "sfu_certificate_san_mismatch"),
        ("expired", "sfu_certificate_expired"),
        ("future", "sfu_certificate_not_yet_valid"),
        ("revoked", "sfu_certificate_revoked"),
    ],
)
def test_extension_mtls_rejects_bad_certificate_at_each_attempt(variant, expected):
    ca_key, ca_cert = _ca()
    other_key, other_ca = _ca("Foreign CA")
    key = ed25519.Ed25519PrivateKey.generate()
    signer_key, signer_ca = (other_key, other_ca) if variant == "foreign" else (ca_key, ca_cert)
    kwargs = {}
    if variant == "wrong_san":
        kwargs["node_id"] = "different-node"
    if variant == "expired":
        kwargs.update(start=-3600, end=-1)
    if variant == "future":
        kwargs.update(start=1, end=3600)
    cert_pem = _leaf(signer_key, signer_ca, key, **kwargs)
    revoked = frozenset({certificate_fingerprint(cert_pem)}) if variant == "revoked" else frozenset()
    policy = SfuNodeTrustPolicy(
        runtime_control_mode=AUTHENTICATED_RUNTIME_EXTENSION,
        activation_enabled=True,
        trusted_ca_certificates=(ca_cert,),
        revoked_certificate_fingerprints=revoked,
    )
    service = _service(policy=policy)
    with pytest.raises(SfuNodeIdentityError, match=expected):
        service.enroll(_extension_command(key, cert_pem), source="192.0.2.7")


def test_extension_mtls_separates_observer_and_rejects_stolen_certificate_claim():
    ca_key, ca_cert = _ca()
    observer_key = ed25519.Ed25519PrivateKey.generate()
    cert_pem = _leaf(ca_key, ca_cert, observer_key, role=SFU_OBSERVER)
    policy = SfuNodeTrustPolicy(
        runtime_control_mode=AUTHENTICATED_RUNTIME_EXTENSION,
        activation_enabled=True,
        trusted_ca_certificates=(ca_cert,),
    )
    service = _service(policy=policy)
    service.enroll(_extension_command(observer_key, cert_pem, role=SFU_OBSERVER), source="192.0.2.8")
    service.authorize_extension_peer(
        node_id="node-a",
        certificate_pem=cert_pem,
        required_role=SFU_OBSERVER,
        transport_peer_verified=True,
    )
    with pytest.raises(SfuNodeIdentityError, match="sfu_identity_role_forbidden"):
        service.authorize_extension_peer(
            node_id="node-a",
            certificate_pem=cert_pem,
            required_role=SFU_CONTROL,
            transport_peer_verified=True,
        )
    with pytest.raises(SfuNodeIdentityError):
        service.authorize_extension_peer(
            node_id="stolen-node-claim",
            certificate_pem=cert_pem,
            required_role=SFU_OBSERVER,
            transport_peer_verified=True,
        )


def test_default_policy_is_observe_only_without_grounded_evidence():
    policy = SfuNodeTrustPolicy.from_file()
    assert policy.runtime_control_mode == LIVEKIT_CONTROL_API
    assert policy.activation_enabled is False
    assert policy.activation_evidence_ids == ()


def test_admin_route_rejects_private_key_before_service_and_never_audits_it(monkeypatch):
    captured = []

    def authenticate(token, *, require_admin=False, **_kwargs):
        if token != "admin-token":
            return False, "missing_token"
        g.user = {"sub": "admin-a", "role": "admin"}
        g.is_admin = True
        return True, "user_jwt"

    monkeypatch.setattr("agent.auth._authenticate_request", authenticate)
    monkeypatch.setattr(
        "agent.routes.webrtc_sfu_node_enrollment.log_audit",
        lambda action, details: captured.append((action, details)),
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.extensions["sfu_node_identity_service"] = _service()
    app.register_blueprint(webrtc_sfu_node_enrollment_bp)
    client = app.test_client()
    denied = client.post("/api/admin/webrtc/sfu-nodes/enroll", json={})
    assert denied.status_code == 401
    private_pem = "-----BEGIN PRIVATE KEY-----\nnever-store-this\n-----END PRIVATE KEY-----"
    response = client.post(
        "/api/admin/webrtc/sfu-nodes/enroll",
        headers={"Authorization": "Bearer admin-token", "Idempotency-Key": "route-private-key-01"},
        json={"actor": "admin-a", "private_key_pem": private_pem},
    )
    assert response.status_code == 400
    assert response.get_json()["data"]["reason_code"] == "sfu_private_key_material_forbidden"
    assert private_pem not in repr(captured)
