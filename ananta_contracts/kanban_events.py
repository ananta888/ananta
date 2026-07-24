"""Versioned wire contracts for the Hub-owned Kanban event projection."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


KANBAN_EVENT_SCHEMA_VERSION = "kanban.event.v1"
KANBAN_EVENT_BATCH_SCHEMA_VERSION = "kanban.event-batch.v1"
KANBAN_AUTH_RENEWAL_SCHEMA_VERSION = "kanban.auth-renewal.v1"


class KanbanEventGapReason(str, Enum):
    BOUNDED_HISTORY_OVERFLOW = "bounded_history_overflow"
    CLIENT_SEQUENCE_AHEAD = "client_sequence_ahead"
    SEQUENCE_GAP = "sequence_gap"


class KanbanEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kanban.event.v1"] = KANBAN_EVENT_SCHEMA_VERSION
    event_id: str = Field(min_length=1, max_length=64)
    board_id: str = Field(min_length=1, max_length=320)
    task_id: str = Field(min_length=1, max_length=255)
    revision: int = Field(ge=0)
    sequence: int = Field(ge=1)
    event_type: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^kanban\.[a-z0-9_.-]+$",
    )
    occurred_at: datetime
    payload: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )

    @field_validator("payload")
    @classmethod
    def validate_minimal_payload(
        cls,
        value: dict[str, str | int | float | bool | None],
    ) -> dict[str, str | int | float | bool | None]:
        if len(value) > 8:
            raise ValueError("kanban event payload must contain at most 8 fields")
        for key, item in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError("kanban event payload key is invalid")
            if isinstance(item, str) and len(item) > 256:
                raise ValueError("kanban event payload string is too long")
        if len(json.dumps(value, sort_keys=True, separators=(",", ":"))) > 2048:
            raise ValueError("kanban event payload is too large")
        return value


class KanbanAuthRenewalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kanban.auth-renewal.v1"] = (
        KANBAN_AUTH_RENEWAL_SCHEMA_VERSION
    )
    mode: Literal["refresh_then_reconnect"] = "refresh_then_reconnect"
    refresh_endpoint: Literal["/refresh-token"] = "/refresh-token"
    authorization_header: Literal["Authorization"] = "Authorization"
    authorization_scheme: Literal["Bearer"] = "Bearer"
    resume_header: Literal["Last-Event-ID"] = "Last-Event-ID"
    renew_before_expiry_seconds: int = Field(default=30, ge=1, le=300)


class KanbanEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kanban.event-batch.v1"] = (
        KANBAN_EVENT_BATCH_SCHEMA_VERSION
    )
    board_id: str = Field(min_length=1, max_length=320)
    requested_after_sequence: int = Field(ge=0)
    next_after_sequence: int = Field(ge=0)
    latest_sequence: int = Field(ge=0)
    events: tuple[KanbanEvent, ...] = ()
    has_more: bool = False
    deduped_events_total: int = Field(default=0, ge=0)
    overflow_events_total: int = Field(default=0, ge=0)
    overflow_reason: KanbanEventGapReason | None = None
    gap_detected: bool = False
    gap_reason: KanbanEventGapReason | None = None
    snapshot_required: bool = False
    snapshot_url: str | None = Field(default=None, max_length=1000)
    auth_renewal: KanbanAuthRenewalContract = Field(
        default_factory=KanbanAuthRenewalContract
    )

    @model_validator(mode="after")
    def validate_reconnect_contract(self) -> "KanbanEventBatch":
        sequences = [event.sequence for event in self.events]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("kanban events must be strictly sequence ordered")
        if self.gap_detected:
            if not self.snapshot_required or not self.snapshot_url:
                raise ValueError("a sequence gap requires REST snapshot fallback")
            if self.gap_reason is None:
                raise ValueError("a sequence gap requires a reason")
            if self.events:
                raise ValueError("partial replay must not accompany a sequence gap")
        elif self.snapshot_required or self.snapshot_url or self.gap_reason:
            raise ValueError("snapshot fallback is only valid for a sequence gap")
        if self.events and self.next_after_sequence != self.events[-1].sequence:
            raise ValueError("next_after_sequence must match the last event")
        if not self.events and self.next_after_sequence != self.requested_after_sequence:
            raise ValueError("empty replay must preserve the requested cursor")
        return self
