"""Meet-specific immutable identity policy, without infrastructure or scheduling."""

TERMINAL = frozenset(
    {"completed", "failed", "cancelled", "verification_failed", "skipped", "aborted", "timeout", "archived"}
)
IDENTITY_FIELDS = (
    "id",
    "task_kind",
    "tenant_id",
    "project_id",
    "parent_task_id",
    "assigned_agent_url",
    "worker_execution_context",
)


def meet_task_write_error(authoritative, candidate):
    """General Tasks retain normal retries; old Meet executions require a new Task."""
    original_kind, proposed_kind = getattr(authoritative, "task_kind", None), getattr(candidate, "task_kind", None)
    if "meet_dialog_session" in (original_kind, proposed_kind) and original_kind != proposed_kind:
        return "meet_dialog_terminal_identity_immutable"
    if original_kind != "meet_dialog_session":
        return None
    before = getattr(authoritative, "worker_execution_context", None) or {}
    after = getattr(candidate, "worker_execution_context", None) or {}
    # Presence matters: null/malformed values cannot become an implicit legacy
    # principal. Existing Tasks may not be retrofitted to a different identity.
    if isinstance(before, dict) and isinstance(after, dict):
        has_preauthorization = "meet_preauthorization" in before
        if has_preauthorization != ("meet_preauthorization" in after) or (
            has_preauthorization and before["meet_preauthorization"] != after["meet_preauthorization"]
        ):
            return "meet_dialog_preauthorization_immutable"
        has_principal = "meet_machine_principal" in before
        if has_principal != ("meet_machine_principal" in after) or (
            has_principal
            and (
                before["meet_machine_principal"] != after["meet_machine_principal"]
                or before.get("meet_role_assignment") != after.get("meet_role_assignment")
                or any(
                    getattr(authoritative, field, None) != getattr(candidate, field, None)
                    for field in (
                        "id",
                        "tenant_id",
                        "project_id",
                        "parent_task_id",
                        "assigned_agent_url",
                        "organization_id",
                        "unit_id",
                        "team_id",
                        "role_slot_id",
                    )
                )
            )
        ):
            return "meet_dialog_principal_immutable"
    elif (
        isinstance(before, dict) and "meet_preauthorization" in before
        or isinstance(after, dict) and "meet_preauthorization" in after
    ):
        return "meet_dialog_preauthorization_immutable"
    elif (
        isinstance(before, dict)
        and "meet_machine_principal" in before
        or isinstance(after, dict)
        and "meet_machine_principal" in after
    ):
        return "meet_dialog_principal_immutable"
    status = getattr(authoritative, "status", None)
    if status not in TERMINAL:
        return None
    unchanged = getattr(candidate, "status", None) == status and all(
        getattr(authoritative, field, None) == getattr(candidate, field, None) for field in IDENTITY_FIELDS
    )
    return None if unchanged else "meet_dialog_terminal_identity_immutable"


def terminal_meet_write_allowed(authoritative, candidate):
    """Compatibility facade; also protects opted-in principals before terminality."""
    return meet_task_write_error(authoritative, candidate) is None


def require_terminal_meet_write(authoritative, candidate):
    error = meet_task_write_error(authoritative, candidate)
    if error is not None:
        raise ValueError(error)
