"""CaseFlow Domain Registry — maps case_type strings to CaseTypeDefinitions."""
from __future__ import annotations

from agent.caseflow.models import CaseTypeDefinition

_registry: dict[str, CaseTypeDefinition] = {}


def register_case_type(defn: CaseTypeDefinition) -> None:
    _registry[defn.case_type] = defn


def get_case_type(case_type: str) -> CaseTypeDefinition | None:
    return _registry.get(case_type)


def list_case_types() -> list[str]:
    return list(_registry.keys())


# Default generic case type
_DEFAULT = CaseTypeDefinition(
    case_type="generic",
    statuses=["new", "active", "waiting", "action_required", "done", "archived"],
    initial_status="new",
    terminal_statuses=["done", "archived"],
)
register_case_type(_DEFAULT)
