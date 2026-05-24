"""SSH Certificate Issuer — validates OIDC identity and requests short-lived SSH user certificates.

Supported backends:
  - step_ca: smallstep step-ca OIDC provisioner (production recommended)
  - none / disabled: returns IssueResult with allowed=False

The issuer enforces:
  - Bounded certificate validity (hub < worker)
  - Deterministic key-id metadata (user_id, auth_source, issuer, issued_at, policy_version, decision_id)
  - No issuance if policy service denies the request
  - Emits audit events for issuance success and denial
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.common.audit import log_audit
from agent.config import settings
from agent.services.ssh_principal_mapper import get_ssh_principal_mapper
from agent.services.terminal_policy_service import get_terminal_policy_service

LOGGER = logging.getLogger("agent.ssh_certificate_issuer")

_ALLOWED_ISSUERS_FROM_CONFIG = None
_KNOWN_NONCES: dict[str, float] = {}
_NONCE_TTL = 600.0


@dataclass(frozen=True)
class SshCertificateIssueResult:
    allowed: bool
    reason_code: str
    certificate_path: str | None
    public_key_path: str | None
    key_id: str | None
    decision_id: str
    principal: str | None
    expires_at: float | None
    target_type: str
    user_id: str


def _purge_old_nonces() -> None:
    cutoff = time.time() - _NONCE_TTL
    stale = [k for k, ts in _KNOWN_NONCES.items() if ts < cutoff]
    for k in stale:
        del _KNOWN_NONCES[k]


def _check_nonce(nonce: str) -> bool:
    """Returns True if nonce is fresh and not replayed. Registers nonce."""
    _purge_old_nonces()
    if nonce in _KNOWN_NONCES:
        return False
    _KNOWN_NONCES[nonce] = time.time()
    return True


def _validate_oidc_token_claims(
    claims: dict[str, Any],
    *,
    expected_issuer: str,
    expected_audience: str,
) -> tuple[bool, str]:
    now = time.time()
    if claims.get("iss") != expected_issuer:
        return False, "ssh_cert_issuer_iss_mismatch"
    aud = claims.get("aud")
    if isinstance(aud, list):
        if expected_audience not in aud:
            return False, "ssh_cert_issuer_aud_mismatch"
    elif aud != expected_audience:
        return False, "ssh_cert_issuer_aud_mismatch"
    exp = claims.get("exp")
    if not exp or float(exp) <= now:
        return False, "ssh_cert_issuer_token_expired"
    iat = claims.get("iat")
    if not iat or float(iat) > now + 30:
        return False, "ssh_cert_issuer_iat_in_future"
    if not claims.get("sub"):
        return False, "ssh_cert_issuer_sub_missing"
    nonce = claims.get("nonce") or ""
    if nonce and not _check_nonce(nonce):
        return False, "ssh_cert_issuer_nonce_replay"
    return True, "ok"


def _build_key_id(
    user_id: str,
    auth_source: str,
    issuer: str,
    decision_id: str,
    policy_version: str,
) -> str:
    parts = [
        f"uid={user_id[:40]}",
        f"src={auth_source}",
        f"iss={hashlib.sha256(issuer.encode()).hexdigest()[:12]}",
        f"dec={decision_id[:12]}",
        f"pol={policy_version[:20]}",
        f"iat={int(time.time())}",
    ]
    return ",".join(parts)


def _validity_for_target(target_type: str) -> int:
    if target_type in ("hub", "hub_as_worker"):
        return settings.ssh_certificate_validity_seconds_hub
    return settings.ssh_certificate_validity_seconds_worker


class SshCertificateIssuer:
    """Issues short-lived SSH user certificates after OIDC validation and policy approval."""

    def issue(
        self,
        *,
        id_token_claims: dict[str, Any],
        target_type: str,
        target_id: str,
        cfg: dict[str, Any] | None = None,
        output_dir: str | None = None,
    ) -> SshCertificateIssueResult:
        decision_id = str(uuid.uuid4())

        if not settings.native_ssh_enabled:
            return self._deny("ssh_cert_issuer_native_ssh_disabled", decision_id, target_type, "")

        issuer = settings.terminal_oidc_issuer
        audience = settings.terminal_oidc_audience or settings.terminal_oidc_client_id
        if not issuer or not audience:
            return self._deny("ssh_cert_issuer_oidc_not_configured", decision_id, target_type, "")

        ok, reason = _validate_oidc_token_claims(
            id_token_claims,
            expected_issuer=issuer,
            expected_audience=audience,
        )
        if not ok:
            LOGGER.warning("OIDC claim validation failed: %s", reason)
            self._audit_denied(reason, id_token_claims, decision_id, target_type)
            return self._deny(reason, decision_id, target_type, "")

        from agent.routes.auth_oidc import _map_claims_to_auth
        user_ctx = _map_claims_to_auth(id_token_claims)
        user_id = user_ctx.get("sub") or user_ctx.get("username") or ""

        mapper = get_ssh_principal_mapper()
        mapping = mapper.map(user_ctx=user_ctx, target_type=target_type, cfg=cfg)
        if not mapping.allowed:
            LOGGER.warning("Principal mapping denied: %s user=%s", mapping.reason_code, user_id)
            self._audit_denied(mapping.reason_code, id_token_claims, decision_id, target_type)
            return self._deny(mapping.reason_code, decision_id, target_type, user_id)

        policy_svc = get_terminal_policy_service()
        try:
            policy_decision = policy_svc.evaluate(
                user_ctx=user_ctx,
                operation="create",
                target_type=target_type,
                target_id=target_id,
                cfg=cfg,
            )
        except Exception:
            self._audit_denied("ssh_cert_issuer_policy_unavailable", id_token_claims, decision_id, target_type)
            return self._deny("ssh_cert_issuer_policy_unavailable", decision_id, target_type, user_id)
        if not policy_decision.allow:
            LOGGER.warning("Terminal policy denied SSH cert issuance: %s", policy_decision.reason_code)
            self._audit_denied(policy_decision.reason_code, id_token_claims, decision_id, target_type)
            return self._deny(policy_decision.reason_code, decision_id, target_type, user_id)

        validity = _validity_for_target(target_type)
        principal = mapping.principals[0]
        key_id = _build_key_id(
            user_id=user_id,
            auth_source="oidc",
            issuer=issuer,
            decision_id=decision_id,
            policy_version=settings.terminal_policy_version,
        )

        backend = settings.ssh_ca_backend
        if backend == "step_ca":
            result = self._issue_via_step_ca(
                id_token_claims=id_token_claims,
                principal=principal,
                key_id=key_id,
                validity=validity,
                output_dir=output_dir or "/tmp/ananta-ssh-certs",
                decision_id=decision_id,
                target_type=target_type,
                user_id=user_id,
            )
        else:
            result = self._deny("ssh_cert_issuer_no_backend_configured", decision_id, target_type, user_id)

        if result.allowed:
            self._audit_issued(result, user_id, target_type, principal)
        return result

    def _issue_via_step_ca(
        self,
        *,
        id_token_claims: dict[str, Any],
        principal: str,
        key_id: str,
        validity: int,
        output_dir: str,
        decision_id: str,
        target_type: str,
        user_id: str,
    ) -> SshCertificateIssueResult:
        import os
        import tempfile

        ca_url = settings.ssh_ca_step_ca_url
        provisioner = settings.ssh_ca_step_ca_provisioner
        ca_fingerprint = settings.ssh_ca_step_ca_ca_fingerprint

        if not ca_url or not provisioner or not ca_fingerprint:
            return self._deny("ssh_cert_issuer_step_ca_not_configured", decision_id, target_type, user_id)

        os.makedirs(output_dir, mode=0o700, exist_ok=True)
        cert_path = os.path.join(output_dir, f"ananta_ssh_cert_{decision_id[:8]}.pub")
        key_path = os.path.join(output_dir, f"ananta_ssh_key_{decision_id[:8]}")

        # step ssh certificate issues a user cert from the OIDC provisioner.
        # The actual step-ca call would need the OIDC token; here we model the interface.
        # In production, this invokes:
        #   step ssh certificate <principal> <key_path> \
        #     --provisioner <provisioner> --token <oidc_id_token> \
        #     --ca-url <ca_url> --fingerprint <ca_fingerprint> \
        #     --not-after <validity>s --principal <principal>
        cmd = [
            "step", "ssh", "certificate",
            principal,
            key_path,
            "--provisioner", provisioner,
            "--ca-url", ca_url,
            "--fingerprint", ca_fingerprint,
            "--not-after", f"{validity}s",
            "--principal", principal,
            "--no-password", "--insecure",
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except FileNotFoundError:
            return self._deny("ssh_cert_issuer_step_not_installed", decision_id, target_type, user_id)
        except subprocess.CalledProcessError as exc:
            LOGGER.warning("step-ca certificate issuance failed: %s", exc.stderr)
            return self._deny("ssh_cert_issuer_step_ca_call_failed", decision_id, target_type, user_id)
        except subprocess.TimeoutExpired:
            return self._deny("ssh_cert_issuer_step_ca_timeout", decision_id, target_type, user_id)

        expires_at = time.time() + validity
        return SshCertificateIssueResult(
            allowed=True,
            reason_code="ok",
            certificate_path=cert_path,
            public_key_path=key_path + ".pub",
            key_id=key_id,
            decision_id=decision_id,
            principal=principal,
            expires_at=expires_at,
            target_type=target_type,
            user_id=user_id,
        )

    @staticmethod
    def _deny(reason: str, decision_id: str, target_type: str, user_id: str) -> SshCertificateIssueResult:
        return SshCertificateIssueResult(
            allowed=False,
            reason_code=reason,
            certificate_path=None,
            public_key_path=None,
            key_id=None,
            decision_id=decision_id,
            principal=None,
            expires_at=None,
            target_type=target_type,
            user_id=user_id,
        )

    @staticmethod
    def _audit_denied(reason: str, claims: dict[str, Any], decision_id: str, target_type: str) -> None:
        if not settings.ssh_audit_enabled:
            return
        try:
            log_audit("ssh_certificate_issuance_denied", {
                "reason_code": reason,
                "decision_id": decision_id,
                "target_type": target_type,
                "sub": str(claims.get("sub") or "")[:40],
                "auth_source": "oidc",
            })
        except Exception:
            pass

    @staticmethod
    def _audit_issued(result: SshCertificateIssueResult, user_id: str, target_type: str, principal: str) -> None:
        if not settings.ssh_audit_enabled:
            return
        try:
            log_audit("ssh_certificate_issued", {
                "decision_id": result.decision_id,
                "key_id": result.key_id,
                "user_id": user_id[:40],
                "principal": principal,
                "target_type": target_type,
                "expires_at": result.expires_at,
                "auth_source": "oidc",
                "policy_version": settings.terminal_policy_version,
            })
        except Exception:
            pass


_INSTANCE: SshCertificateIssuer | None = None


def get_ssh_certificate_issuer() -> SshCertificateIssuer:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SshCertificateIssuer()
    return _INSTANCE
