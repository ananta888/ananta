"""Schema identifiers, task kind and lifecycle status sets of delegated mail operations."""

from __future__ import annotations

MAIL_TASK_SCHEMA = "ananta.mail_task.v1"
MAIL_TASK_RESULT_SCHEMA = "ananta.mail_task_result.v1"
MAIL_TASK_KIND = "mail_operation"
MAIL_OPERATIONS = frozenset(
    {"discovery", "sync", "body", "mutation", "migration", "cutover", "diagnose"}
)
MAIL_PROVIDERS = frozenset({"imap", "jmap"})
MAIL_TASK_ACTIVE_STATUSES = frozenset(
    {"created", "todo", "blocked", "blocked_by_dependency", "assigned", "in_progress", "running"}
)
MAIL_TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

__all__ = [
    "MAIL_OPERATIONS",
    "MAIL_PROVIDERS",
    "MAIL_TASK_ACTIVE_STATUSES",
    "MAIL_TASK_KIND",
    "MAIL_TASK_RESULT_SCHEMA",
    "MAIL_TASK_SCHEMA",
    "MAIL_TASK_TERMINAL_STATUSES",
]
