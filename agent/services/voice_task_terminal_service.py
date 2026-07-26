from __future__ import annotations

from typing import Any, Mapping

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TaskDB
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_status_service import normalize_task_status

_VOICE_TASK_KINDS = frozenset(
    {
        "voice_live_run",
        "voice_transcription",
        "voice_generative_judge",
        "voice_generative_corrector",
        "restricted_inference",
        "speech_adaptation",
    }
)
_MUTABLE_FIELDS = frozenset(
    {
        "last_output",
        "status_reason_code",
        "status_reason_details",
        "verification_status",
    }
)


class VoiceTaskTerminalService:
    """Apply terminal Voice task state only when the owned row still exists."""

    def update_existing(
        self,
        task_id: str,
        status: str,
        *,
        event_type: str,
        event_actor: str = "hub",
        event_details: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> bool:
        normalized_status = normalize_task_status(status)
        recovery_completion = False
        with Session(engine) as session:
            task = session.exec(
                select(TaskDB).where(TaskDB.id == task_id)
            ).first()
            if task is None or str(task.task_kind or "") not in _VOICE_TASK_KINDS:
                return False
            previous_status = str(task.status or "")
            allowed, _reason = can_transition_to(previous_status, normalized_status)
            if previous_status != normalized_status and not allowed:
                recovery_completion = bool(
                    previous_status == "failed"
                    and normalized_status == "completed"
                    and ("last_output" in fields or "verification_status" in fields)
                )
                if not recovery_completion:
                    return False
        from agent.services.task_runtime_service import (
            compare_and_set_local_task_status,
        )

        return compare_and_set_local_task_status(
            task_id,
            normalized_status,
            expected_statuses={previous_status},
            authoritative_predicate=lambda authoritative: (
                str(authoritative.task_kind or "")
                in _VOICE_TASK_KINDS
            ),
            event_type=event_type,
            event_actor=event_actor,
            event_details=dict(event_details or {}),
            force=recovery_completion,
            **{
                key: value
                for key, value in fields.items()
                if key in _MUTABLE_FIELDS
            },
        )


voice_task_terminal_service = VoiceTaskTerminalService()


def get_voice_task_terminal_service() -> VoiceTaskTerminalService:
    return voice_task_terminal_service
