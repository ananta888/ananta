from enum import Enum

class TaskStatus(str, Enum):
    TODO = "todo"
    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DELEGATED = "delegated"
    WAITING_FOR_REVIEW = "waiting_for_review"
    VERIFICATION_FAILED = "verification_failed"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"
    PROPOSING = "proposing"
    UPDATED = "updated"
