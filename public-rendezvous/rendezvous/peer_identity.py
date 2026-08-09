"""Canonical account and device identity helpers for Public Pair.

OIDC authenticates an account principal.  Pair transport addresses a concrete
device membership instead, so the two identifiers deliberately use different
domains and prefixes.
"""

from __future__ import annotations

import hashlib
import re

_ACCOUNT_ID = re.compile(r"oidc:[0-9a-f]{64}\Z")
_DEVICE_PEER_ID = re.compile(r"peer:[0-9a-f]{64}\Z")
_MEMBERSHIP_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def is_canonical_account_id(value: str) -> bool:
    return _ACCOUNT_ID.fullmatch(str(value or "")) is not None


def is_device_peer_id(value: str) -> bool:
    return _DEVICE_PEER_ID.fullmatch(str(value or "")) is not None


def is_membership_capability(value: str) -> bool:
    return _MEMBERSHIP_CAPABILITY.fullmatch(str(value or "")) is not None


def canonical_device_peer_id(
    account_id: str,
    device_key_fingerprint: str,
) -> str:
    """Bind a transport peer to one verified account/device key."""
    normalized_account_id = str(account_id or "").strip()
    normalized_fingerprint = str(device_key_fingerprint or "").strip().lower()
    if not is_canonical_account_id(normalized_account_id):
        raise ValueError("oidc_identity_required")
    if not normalized_fingerprint:
        raise ValueError("device_identity_required")
    material = (
        b"ananta.public-rendezvous.device-peer-id.v2\0"
        + normalized_account_id.encode("utf-8")
        + b"\0"
        + normalized_fingerprint.encode("ascii")
    )
    return "peer:" + hashlib.sha256(material).hexdigest()


def membership_capability_digest(
    session_id: str,
    account_id: str,
    peer_id: str,
    capability: str,
) -> str:
    """Hash a bearer capability with all server-verified membership bindings."""
    if not is_canonical_account_id(account_id) or not is_device_peer_id(peer_id):
        raise ValueError("membership_identity_invalid")
    if not is_membership_capability(capability):
        raise ValueError("membership_capability_invalid")
    material = (
        b"ananta.public-rendezvous.membership-capability.v2\0"
        + str(session_id or "").encode("utf-8")
        + b"\0"
        + account_id.encode("ascii")
        + b"\0"
        + peer_id.encode("ascii")
        + b"\0"
        + capability.encode("ascii")
    )
    return hashlib.sha256(material).hexdigest()


def owner_capability_lookup_digest(
    account_id: str,
    peer_id: str,
    capability: str,
) -> str:
    """Create a session-independent idempotency lookup without storing a secret."""
    if not is_canonical_account_id(account_id) or not is_device_peer_id(peer_id):
        raise ValueError("membership_identity_invalid")
    if not is_membership_capability(capability):
        raise ValueError("membership_capability_invalid")
    material = (
        b"ananta.public-rendezvous.owner-capability-lookup.v2\0"
        + account_id.encode("ascii")
        + b"\0"
        + peer_id.encode("ascii")
        + b"\0"
        + capability.encode("ascii")
    )
    return hashlib.sha256(material).hexdigest()
