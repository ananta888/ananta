from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.models import BlueprintArtifactDefinition, BlueprintRoleDefinition


def _validate_blueprint_roles(
    roles: list[BlueprintRoleDefinition],
) -> tuple[bool, tuple | None]:
    # All roles must have unique names
    names_count = defaultdict(int)
    for role in roles:
        names_count[role.name.strip().lower()] += 1
    duplicates = [name for name, count in names_count.items() if count > 1]
    if duplicates:
        return False, ("duplicate_role_names", 400, {"names": duplicates})

    # At least one role must be non-optional
    if not any(role.is_required for role in roles):
        return False, ("at_least_one_required_role", 400)
    return True, None


def _validate_blueprint_artifacts(
    artifacts: list[BlueprintArtifactDefinition],
) -> tuple[bool, tuple | None]:
    # All artifacts must have unique titles
    titles_count = defaultdict(int)
    for artifact in artifacts:
        titles_count[artifact.title.strip().lower()] += 1
    duplicates = [title for title, count in titles_count.items() if count > 1]
    if duplicates:
        return False, ("duplicate_artifact_titles", 400, {"titles": duplicates})
    return True, None
