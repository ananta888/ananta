"""Explicit compatibility between legacy context and memory sensitivity labels."""

from ananta_contracts.context_access_policy import Sensitivity
from ananta_contracts.memory_access import MemorySensitivity


def memory_sensitivity(label: str) -> MemorySensitivity:
    # The old context contract names internal data project_internal. This
    # changes vocabulary only: namespace and current Hub grants still apply.
    return MemorySensitivity("internal" if label == "project_internal" else label)


def context_sensitivity(label: str) -> Sensitivity:
    return Sensitivity("project_internal" if label == "internal" else label)
