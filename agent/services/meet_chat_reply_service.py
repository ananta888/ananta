"""Materialize admitted chat replies through the existing Hub media task port."""

import time
import uuid
from typing import Protocol

from agent.services.meet_chat_admission import ChatAdmission, ChatAuthorityPort
from agent.services.meet_contract import MeetError
from agent.services.meet_media_result import validate_response_budget, validate_result
from agent.services.meet_turn_service import MediaTaskPort, MediaWorkerPort
from ananta_contracts.meet_speech import validate_speech_profile
from worker.meet_media.contract import SCHEMA, validate_turn


class ChatDispatchPort(Protocol):
    def claim(self, reservation, task_id: str, lease_id: str) -> None: ...
    def finish(self, intent_id: str, task_id: str, lease_id: str, state: str) -> bool: ...


class MeetChatReplyService:
    def __init__(
        self,
        authority: ChatAuthorityPort,
        dispatches: ChatDispatchPort,
        binding,
        worker: MediaWorkerPort,
        tasks: MediaTaskPort,
        clock=time.time,
        speech_profile=None,
    ):
        self.authority, self.dispatches, self.binding = authority, dispatches, binding
        self.worker, self.tasks, self.clock = worker, tasks, clock
        self.speech_profile = validate_speech_profile(speech_profile) if speech_profile is not None else None

    def _require_current(self, principal, reservation):
        scope = reservation.scope
        if principal.tenant_id != scope.tenant_id:
            raise MeetError("meet_chat_principal_mismatch", 403)
        self.binding.require_write_access(principal, scope.project_id, scope.task_id)
        current = self.authority.current(scope.session_id)
        if (
            current is None
            or current.scope != scope
            or current.policy.mode == "off"
            or current.policy.max_reply_chars != reservation.max_reply_chars
            or current.policy.max_output_tokens != reservation.max_output_tokens
            or self.clock() * 1000 >= scope.deadline_ms
        ):
            raise MeetError("meet_chat_authority_changed", 409)

    def execute(self, principal, admission: ChatAdmission):
        if admission.code != "reserved" or admission.reservation is None or not isinstance(admission.text, str):
            raise MeetError("meet_chat_admission_required", 409)
        reservation = admission.reservation
        self._require_current(principal, reservation)
        scope = reservation.scope
        turn = {
            "schema": SCHEMA,
            "task_id": str(uuid.uuid4()),
            "lease_id": str(uuid.uuid4()),
            "tenant_id": scope.tenant_id,
            "project_id": scope.project_id,
            "binding_task_id": scope.task_id,
            "deadline": int(min(self.clock() + 115, scope.deadline_ms / 1000)),
            "text": admission.text,
            "response_limits": {
                "max_reply_chars": reservation.max_reply_chars,
                "max_output_tokens": reservation.max_output_tokens,
            },
        }
        # No meeting grant: generation does not silently publish through v1.
        if self.speech_profile is not None:
            turn["speech_profile"] = dict(self.speech_profile)
        validate_turn(turn, self.clock())
        self.dispatches.claim(reservation, turn["task_id"], turn["lease_id"])
        task_turn = turn | {
            "hub_chat_binding": {
                "intent_id": reservation.intent_id,
                "session_id": scope.session_id,
                "generation": scope.generation,
                "policy_revision": scope.policy_revision,
            }
        }
        started = False
        try:
            self._require_current(principal, reservation)
            self.tasks.start(task_turn, principal.subject_id)
            started = True
            self._require_current(principal, reservation)
            result = self.worker.execute(turn)
            validate_result(result)
            validate_response_budget(turn, result)
            if (
                result["task_id"] != turn["task_id"]
                or result["lease_id"] != turn["lease_id"]
                or "meeting" in result
                or self.clock() >= turn["deadline"]
            ):
                raise MeetError("meet_chat_result_stale", 409)
            self._require_current(principal, reservation)
            if not self.tasks.finish(task_turn, "completed"):
                raise MeetError("meet_chat_task_cancelled", 409)
            if not self.dispatches.finish(reservation.intent_id, turn["task_id"], turn["lease_id"], "completed"):
                raise MeetError("meet_chat_dispatch_conflict", 409)
            self._require_current(principal, reservation)
            return {
                "schema": "ananta.meet-chat-reply.v1",
                "intent_id": reservation.intent_id,
                "session_id": scope.session_id,
                "generation": scope.generation,
                "reply_to": reservation.message_id,
                "media": result,
                "published": False,
            }
        except Exception:
            try:
                if started:
                    self.tasks.finish(task_turn, "failed")
            finally:
                self.dispatches.finish(reservation.intent_id, turn["task_id"], turn["lease_id"], "failed")
            raise
