from __future__ import annotations

from agent.services import ssh_access_audit_service as saa


def test_record_certificate_issued_emits_expected_fields(monkeypatch):
    events = []
    monkeypatch.setattr(saa.settings, "ssh_audit_enabled", True)
    monkeypatch.setattr(saa, "log_audit", lambda action, details=None: events.append((action, details or {})))

    svc = saa.SshAccessAuditService()
    svc.record_certificate_issued(
        user_id="user-1",
        key_id="kid-1",
        principal="ananta-worker-user-1",
        target_type="worker",
        decision_id="dec-1",
        expires_at=123.0,
        policy_version="terminal-policy.v1",
    )

    assert len(events) == 1
    action, details = events[0]
    assert action == saa.EVT_SSH_CERT_ISSUED
    assert details["key_id"] == "kid-1"
    assert details["auth_source"] == "oidc"


def test_record_policy_denied_noop_when_audit_disabled(monkeypatch):
    events = []
    monkeypatch.setattr(saa.settings, "ssh_audit_enabled", False)
    monkeypatch.setattr(saa, "log_audit", lambda action, details=None: events.append((action, details or {})))

    svc = saa.SshAccessAuditService()
    svc.record_policy_denied(
        user_id="user-1",
        reason_code="denied",
        operation="create",
        target_type="worker",
        target_id="alpha",
    )
    assert events == []


def test_emitted_audit_record_omits_secret_fields(monkeypatch):
    events = []
    monkeypatch.setattr(saa.settings, "ssh_audit_enabled", True)
    monkeypatch.setattr(saa, "log_audit", lambda action, details=None: events.append((action, details or {})))

    svc = saa.SshAccessAuditService()
    svc.record_certificate_denied(
        user_id="user-1",
        reason_code="terminal_permission_denied",
        target_type="worker",
        decision_id="dec-1",
    )

    assert len(events) == 1
    _action, details = events[0]
    forbidden = {"id_token", "access_token", "refresh_token", "private_key", "password"}
    assert forbidden.isdisjoint(set(details.keys()))
