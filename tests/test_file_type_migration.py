from __future__ import annotations

import copy
from pathlib import Path

from ananta_contracts.file_type_migration import affected_paths, compare_file_type_registries
from ananta_contracts.file_type_support import FileTypeSupportRegistry, load_file_type_support_registry


def _registry() -> FileTypeSupportRegistry:
    return load_file_type_support_registry(Path(__file__).resolve().parents[1])


def test_registry_diff_invalidates_only_changed_format():
    previous = _registry()
    current_raw = copy.deepcopy(previous.as_dict())
    current_raw["registry_version"] = "next"
    markdown = next(item for item in current_raw["formats"] if item["format_id"] == "markdown")
    markdown["parser_strategy"] = "documentation-structure-v2"
    current = FileTypeSupportRegistry.from_mapping(current_raw)

    plan = compare_file_type_registries(previous, current)

    assert plan.changed_format_ids == ("markdown",)
    assert plan.added_format_ids == ()
    assert plan.removed_format_ids == ()
    assert plan.requires_migration is True
    assert plan.as_dict()["strategy"] == "targeted_file_type_invalidation"


def test_affected_paths_keep_unrelated_types_cached():
    previous = _registry()
    current_raw = copy.deepcopy(previous.as_dict())
    current_raw["registry_version"] = "next"
    markdown = next(item for item in current_raw["formats"] if item["format_id"] == "markdown")
    markdown["known_limits"] = [*markdown["known_limits"], "changed"]
    current = FileTypeSupportRegistry.from_mapping(current_raw)

    paths = affected_paths(
        ["README.md", "agent/app.py", "config/settings.yaml"],
        previous=previous,
        current=current,
    )

    assert paths == ("README.md",)


def test_identical_registry_requires_no_migration():
    registry = _registry()

    plan = compare_file_type_registries(registry, registry)

    assert plan.requires_migration is False
    assert plan.affected_format_ids == ()
    assert affected_paths(["README.md"], previous=registry, current=registry) == ()
