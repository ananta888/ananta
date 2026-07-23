"""Versioned contracts for the hub-owned Kanban task projection."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


KANBAN_SCHEMA_VERSION = "kanban.v1"


class KanbanContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def reject_unsafe_text_payloads(self) -> "KanbanContractModel":
        """Reject executable markup while preserving ordinary plain text."""

        for field_name in ("title", "description", "body", "reason", "outcome"):
            value = getattr(self, field_name, None)
            if not isinstance(value, str):
                continue
            if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", value):
                raise ValueError(f"{field_name} must not contain HTML")
            compact = re.sub(r"[\x00-\x20]+", "", value).lower()
            if any(
                scheme in compact
                for scheme in ("javascript:", "vbscript:", "data:text/html", "file:")
            ):
                raise ValueError(f"{field_name} contains an unsafe URL scheme")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
                raise ValueError(f"{field_name} contains control characters")
        return self


class KanbanScopeType(str, Enum):
    HUB = "hub"
    GOAL = "goal"
    TEAM = "team"


class KanbanColumnId(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class KanbanCapability(str, Enum):
    READ = "kanban.read"
    WRITE = "kanban.write"
    ASSIGN = "kanban.assign"
    COMMENT = "kanban.comment"
    ADMIN = "kanban.admin"


class KanbanColumn(KanbanContractModel):
    id: KanbanColumnId
    title: str
    statuses: tuple[str, ...]
    card_count: int = Field(ge=0)


class KanbanAssignee(KanbanContractModel):
    id: str
    name: str | None = None
    url: str | None = None


class KanbanCard(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    id: str
    board_id: str
    title: str
    description: str | None = None
    status: str
    column_id: KanbanColumnId
    position: int = Field(ge=0)
    revision: int = Field(ge=0)
    priority: str
    assignee: KanbanAssignee | None = None
    labels: tuple[str, ...] = ()
    blocked: bool = False
    dependencies: tuple[str, ...] = ()
    comment_count: int = Field(default=0, ge=0)
    activity_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class KanbanBoardSummary(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    id: str
    name: str
    scope_type: KanbanScopeType
    scope_id: str | None = None
    revision: str
    card_count: int = Field(ge=0)
    capabilities: tuple[KanbanCapability, ...] = ()


class KanbanBoard(KanbanBoardSummary):
    columns: tuple[KanbanColumn, ...]


class KanbanBoardPage(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    items: tuple[KanbanBoardSummary, ...]
    next_cursor: str | None = None


class KanbanCardPage(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    board_id: str
    board_revision: str
    items: tuple[KanbanCard, ...]
    next_cursor: str | None = None


class KanbanComment(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    id: str
    card_id: str
    author_id: str
    body: str
    created_at: datetime


class KanbanCommentPage(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    board_id: str
    card_id: str
    board_revision: str
    items: tuple[KanbanComment, ...]
    next_cursor: str | None = None


class KanbanActivity(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    id: str
    card_id: str
    event_type: str
    actor_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class KanbanActivityPage(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    board_id: str
    card_id: str
    board_revision: str
    items: tuple[KanbanActivity, ...]
    next_cursor: str | None = None


class KanbanCapabilities(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    board_id: str | None = None
    capabilities: tuple[KanbanCapability, ...]


class CreateBoardCommand(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    scope_type: KanbanScopeType
    scope_id: str | None = Field(default=None, min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_scope(self) -> "CreateBoardCommand":
        if self.scope_type == KanbanScopeType.HUB and self.scope_id is not None:
            raise ValueError("hub boards must not define scope_id")
        if self.scope_type != KanbanScopeType.HUB and self.scope_id is None:
            raise ValueError("goal and team boards require scope_id")
        return self


class CreateCardCommand(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    priority: str = Field(default="Medium", min_length=1, max_length=32)
    position: int | None = Field(default=None, ge=0)
    dependencies: tuple[str, ...] = Field(default=(), max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=128)


class RevisionedCardCommand(KanbanContractModel):
    schema_version: str = KANBAN_SCHEMA_VERSION
    board_id: str = Field(min_length=1, max_length=320)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class MoveCardCommand(RevisionedCardCommand):
    column_id: KanbanColumnId
    position: int = Field(ge=0)


class AssignCardCommand(RevisionedCardCommand):
    assignee_id: str | None = Field(default=None, max_length=255)


class CommentCardCommand(RevisionedCardCommand):
    body: str = Field(min_length=1, max_length=10_000)


class SetDependenciesCommand(RevisionedCardCommand):
    dependencies: tuple[str, ...] = Field(default=(), max_length=100)


class BlockCardCommand(RevisionedCardCommand):
    reason: str = Field(min_length=1, max_length=2_000)
    dependencies: tuple[str, ...] = Field(default=(), max_length=100)


class CompleteCardCommand(RevisionedCardCommand):
    outcome: str | None = Field(default=None, max_length=2_000)
