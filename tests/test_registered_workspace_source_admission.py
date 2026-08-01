from __future__ import annotations

from types import SimpleNamespace

from agent.services.registered_workspace_source_admission import (
    RegisteredWorkspaceSourceAdmissionService,
)
from agent.services.source_admission_service import SourceAdmissionBudgets


def test_workspace_admission_policy_digest_is_stable_and_budget_bound() -> None:
    first = SourceAdmissionBudgets(
        max_files=10,
        allowed_file_types=frozenset({"py", "txt"}),
    )
    same = SourceAdmissionBudgets(
        max_files=10,
        allowed_file_types=frozenset({"txt", "py"}),
    )
    changed = SourceAdmissionBudgets(
        max_files=11,
        allowed_file_types=frozenset({"py", "txt"}),
    )

    assert (
        RegisteredWorkspaceSourceAdmissionService._policy_digest(first)
        == RegisteredWorkspaceSourceAdmissionService._policy_digest(same)
    )
    assert (
        RegisteredWorkspaceSourceAdmissionService._policy_digest(first)
        != RegisteredWorkspaceSourceAdmissionService._policy_digest(changed)
    )
