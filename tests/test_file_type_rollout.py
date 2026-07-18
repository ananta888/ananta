from __future__ import annotations

from pathlib import Path

import pytest

from ananta_contracts.file_type_rollout import (
    FileTypeRolloutPolicy,
    FileTypeRolloutPolicyError,
)
from ananta_contracts.file_type_support import load_file_type_support_registry


def _registry():
    return load_file_type_support_registry(Path(__file__).resolve().parents[1])


def test_rollout_policy_allows_only_explicit_active_descriptors() -> None:
    registry = _registry()
    policy = FileTypeRolloutPolicy.build(
        registry,
        priorities={"P0", "P1"},
        enabled_format_ids={"markdown", "csv"},
    )

    assert policy.allows(registry.descriptor("markdown")) is True
    assert policy.allows(registry.descriptor("csv")) is True
    assert policy.allows(registry.descriptor("python")) is False


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"priorities": {"P9"}}, "unknown_file_type_priority"),
        (
            {
                "priorities": {"P0"},
                "enabled_format_ids": {"missing"},
            },
            "unknown_file_type_format",
        ),
        (
            {
                "priorities": {"P0"},
                "enabled_format_ids": {"python"},
                "disabled_format_ids": {"python"},
            },
            "conflicting_file_type_format",
        ),
    ],
)
def test_rollout_policy_rejects_invalid_configuration(kwargs, reason) -> None:
    with pytest.raises(FileTypeRolloutPolicyError, match=reason):
        FileTypeRolloutPolicy.build(_registry(), **kwargs)
