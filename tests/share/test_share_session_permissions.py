from __future__ import annotations

import pytest

from agent.services.share_session_permissions import (
    CANONICAL_PERMISSION_KEYS,
    LEGACY_ALIAS_EXPIRES_AT,
    PermissionContractError,
    ShareSessionPermissionService,
    normalize_share_permissions,
)


@pytest.mark.parametrize("permission", CANONICAL_PERMISSION_KEYS)
def test_each_canonical_permission_is_independent(permission: str) -> None:
    result = normalize_share_permissions({key: key == permission for key in CANONICAL_PERMISSION_KEYS})
    assert result.values[permission] is True
    assert sum(result.values.values()) == 1


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("cursor", "remote_cursor"),
        ("control", "remote_control"),
        ("artifact_view", "artifact_share"),
        ("annotation", "artifact_share"),
    ],
)
def test_legacy_aliases_are_time_bounded_and_canonical(alias: str, canonical: str) -> None:
    migrated = normalize_share_permissions({alias: True}, now=LEGACY_ALIAS_EXPIRES_AT - 1)
    assert migrated.values[canonical] is True
    assert alias in migrated.legacy_aliases_used
    with pytest.raises(PermissionContractError, match="permission_alias_expired"):
        normalize_share_permissions({alias: True}, now=LEGACY_ALIAS_EXPIRES_AT)


@pytest.mark.parametrize(
    "permissions,reason",
    [
        ({"rogue": True}, "permission_unknown"),
        ({"chat": "false"}, "permission_value_not_boolean"),
        ({"remote_control": False, "control": True}, "permission_conflict"),
        ({"artifact_view": True, "annotation": False}, "permission_conflict"),
    ],
)
def test_invalid_or_ambiguous_documents_fail_closed(permissions: dict, reason: str) -> None:
    with pytest.raises(PermissionContractError) as exc:
        normalize_share_permissions(permissions, now=LEGACY_ALIAS_EXPIRES_AT - 1)
    assert exc.value.reason_code == reason


def test_cache_is_invalidated_before_next_authorization_check() -> None:
    service = ShareSessionPermissionService()
    assert service.allows("s1", {"remote_control": True}, "remote_control")
    service.invalidate("s1")
    assert not service.allows("s1", {"remote_control": False}, "remote_control")
    assert not service.allows("s1", {"remote_control": True}, "unknown")
