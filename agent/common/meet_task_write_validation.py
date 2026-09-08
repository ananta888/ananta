"""Meet-specific terminal identity policy, without infrastructure or scheduling."""

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


def terminal_meet_write_allowed(authoritative, candidate):
    """General Tasks retain normal retries; old Meet executions require a new Task."""
    original_kind, proposed_kind = getattr(authoritative, "task_kind", None), getattr(candidate, "task_kind", None)
    if "meet_dialog_session" in (original_kind, proposed_kind) and original_kind != proposed_kind:
        return False  # Neither rename away before terminality nor convert a retried ordinary Task back.
    if original_kind != "meet_dialog_session":
        return True
    status = getattr(authoritative, "status", None)
    if status not in TERMINAL:
        return True
    return getattr(candidate, "status", None) == status and all(
        getattr(authoritative, field, None) == getattr(candidate, field, None) for field in IDENTITY_FIELDS
    )


def require_terminal_meet_write(authoritative, candidate):
    if not terminal_meet_write_allowed(authoritative, candidate):
        raise ValueError("meet_dialog_terminal_identity_immutable")
