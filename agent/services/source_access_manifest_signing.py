"""Rotatable signing for Hub-issued source enforcement manifests."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Mapping


_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(
    r"^v1\.([A-Za-z0-9][A-Za-z0-9._:-]{0,63})\.([0-9a-f]{64})$"
)


class SourceAccessManifestSigningError(ValueError):
    pass


@dataclass(frozen=True)
class SourceAccessSigningKey:
    key_id: str
    secret: bytes

    def __post_init__(self) -> None:
        if not _KEY_ID.fullmatch(self.key_id):
            raise SourceAccessManifestSigningError("signing_key_id_invalid")
        if not isinstance(self.secret, bytes) or len(self.secret) < 32:
            raise SourceAccessManifestSigningError(
                "signing_key_material_invalid"
            )

    def __repr__(self) -> str:
        return (
            "SourceAccessSigningKey("
            f"key_id={self.key_id!r}, secret=<redacted>)"
        )


class HubSourceAccessManifestSigner:
    def __init__(self, key: SourceAccessSigningKey) -> None:
        self._key = key

    def sign(self, *, manifest_digest: str) -> str:
        if not _DIGEST.fullmatch(str(manifest_digest or "")):
            raise SourceAccessManifestSigningError("manifest_digest_invalid")
        mac = hmac.new(
            self._key.secret,
            manifest_digest.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"v1.{self._key.key_id}.{mac}"


class WorkerSourceAccessManifestVerifier:
    """Worker-side verification only; it makes no grant or policy decision."""

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        self._keys: dict[str, bytes] = {}
        for key_id, secret in keys.items():
            key = SourceAccessSigningKey(key_id=key_id, secret=secret)
            self._keys[key.key_id] = key.secret
        if not self._keys:
            raise SourceAccessManifestSigningError("verification_keys_required")

    def verify(
        self,
        *,
        manifest_digest: str,
        signature: str,
    ) -> bool:
        if not _DIGEST.fullmatch(str(manifest_digest or "")):
            return False
        match = _SIGNATURE.fullmatch(str(signature or ""))
        if match is None:
            return False
        key_id, supplied_mac = match.groups()
        secret = self._keys.get(key_id)
        if secret is None:
            return False
        expected_mac = hmac.new(
            secret,
            manifest_digest.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_mac, supplied_mac)

    def verify_manifest(self, manifest: Mapping[str, object]) -> bool:
        """Verify integrity only; grant and policy decisions stay in the Hub."""

        from agent.services.source_access_enforcement import (
            SourceAccessRequest,
            source_access_binding_digest,
        )
        from ananta_contracts.source_control import (
            GrantOperation,
            GrantTransformation,
        )

        expected_fields = {
            "schema",
            "authority",
            "tenant_id",
            "project_id",
            "source_revision_id",
            "source_revision_digest",
            "destination_id",
            "destination_digest",
            "source_access_grant_id",
            "source_access_grant_digest",
            "grant_expires_at_epoch_ms",
            "operation",
            "transformation",
            "purpose",
            "policy_version",
            "policy_digest",
            "content_manifest_id",
            "content_manifest_digest",
            "assignment_id",
            "lease_id",
            "binding_digest",
            "signature",
        }
        if set(manifest) != expected_fields:
            return False
        if (
            manifest.get("schema")
            != "ananta.source-control.enforcement-manifest.v1"
            or manifest.get("authority") != "hub"
        ):
            return False
        try:
            request = SourceAccessRequest(
                tenant_id=str(manifest["tenant_id"]),
                project_id=str(manifest["project_id"]),
                source_revision_id=str(
                    manifest["source_revision_id"]
                ),
                source_revision_digest=str(
                    manifest["source_revision_digest"]
                ),
                destination_id=str(manifest["destination_id"]),
                destination_digest=str(
                    manifest["destination_digest"]
                ),
                source_access_grant_id=str(
                    manifest["source_access_grant_id"]
                ),
                source_access_grant_digest=str(
                    manifest["source_access_grant_digest"]
                ),
                operation=GrantOperation(str(manifest["operation"])),
                transformation=GrantTransformation(
                    str(manifest["transformation"])
                ),
                purpose=str(manifest["purpose"]),
                policy_version=str(manifest["policy_version"]),
                policy_digest=str(manifest["policy_digest"]),
                manifest_id=str(manifest["content_manifest_id"]),
                manifest_digest=str(
                    manifest["content_manifest_digest"]
                ),
                assignment_id=str(manifest["assignment_id"]),
                lease_id=str(manifest["lease_id"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        expiry = manifest.get("grant_expires_at_epoch_ms")
        if (
            isinstance(expiry, bool)
            or not isinstance(expiry, int)
            or expiry <= 0
        ):
            return False
        actual_digest = source_access_binding_digest(
            request,
            grant_expires_at_epoch_ms=expiry,
        )
        supplied_digest = str(manifest.get("binding_digest") or "")
        if not hmac.compare_digest(actual_digest, supplied_digest):
            return False
        return self.verify(
            manifest_digest=supplied_digest,
            signature=str(manifest.get("signature") or ""),
        )
