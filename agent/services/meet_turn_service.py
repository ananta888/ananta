"""Hub policy and task lifecycle for bounded local chat/media responses."""

import time
import uuid
from typing import Protocol

from agent.services.meet_contract import MeetError
from worker.meet_media.contract import SCHEMA, validate_turn


class MediaWorkerPort(Protocol):
    def execute(self, turn: dict) -> dict: ...


class MediaTaskPort(Protocol):
    def start(self, turn: dict, actor: str) -> None: ...
    def finish(self, turn: dict, status: str) -> bool: ...


class PersonaImagePort(Protocol):
    def prepare(self, principal, project: str, artifact_id: str, purpose: str) -> dict: ...
    def require_current(self, principal, project: str, reference: dict, purpose: str) -> None: ...


class MeetTurnService:
    def __init__(
        self,
        binding,
        worker: MediaWorkerPort,
        tasks: MediaTaskPort,
        allowed_scopes,
        clock=time.time,
        grant_issuer=None,
        persona_images: PersonaImagePort | None = None,
    ):
        self.binding, self.worker, self.tasks = binding, worker, tasks
        self.allowed_scopes = frozenset(allowed_scopes)
        self.clock = clock
        self.grant_issuer = grant_issuer
        self.persona_images = persona_images

    def execute(self, principal, project, payload, task=""):
        # This is generation authority, not authority to join or publish in Meet.
        self.binding.require_write_access(principal, project, task)
        if (principal.tenant_id, project) not in self.allowed_scopes:
            raise MeetError("meet_media_policy_denied", 403)
        if (
            not isinstance(payload, dict)
            or set(payload) - {"publish_to_meet", "persona_image_id"} != {"text"}
            or type(payload.get("publish_to_meet", False)) is not bool
        ):
            raise MeetError("meet_turn_payload_invalid")
        turn = {
            "schema": SCHEMA,
            "task_id": str(uuid.uuid4()),
            "lease_id": str(uuid.uuid4()),
            "tenant_id": principal.tenant_id,
            "project_id": project,
            "deadline": int(self.clock()) + 115,
            "text": payload["text"],
        }
        if task:
            turn["binding_task_id"] = task
        image_purpose = "publish" if payload.get("publish_to_meet") else "preview"
        if "persona_image_id" in payload:
            import re

            if (
                self.persona_images is None
                or not isinstance(payload["persona_image_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", payload["persona_image_id"])
            ):
                raise MeetError("meet_persona_image_unavailable", 403)
            turn["persona_image"] = self.persona_images.prepare(
                principal, project, payload["persona_image_id"], image_purpose
            )
        if payload.get("publish_to_meet"):
            if self.grant_issuer is None:
                raise MeetError("meet_machine_publication_disabled", 403)
            turn["meeting"] = self.grant_issuer.issue(turn, self.binding, principal, self.clock(), task=task)
        try:
            validate_turn(turn, self.clock())
        except ValueError as exc:
            raise MeetError(str(exc)) from None
        self.tasks.start(turn, principal.subject_id)
        try:
            result = self.worker.execute(turn)
            if (
                self.clock() >= turn["deadline"]
                or result.get("task_id") != turn["task_id"]
                or result.get("lease_id") != turn["lease_id"]
            ):
                raise MeetError("meet_turn_result_stale", 409)
            # Recheck project access before disclosing generated media.
            self.binding.require_write_access(principal, project, task)
            if "persona_image" in turn:
                self.persona_images.require_current(
                    principal, project, turn["persona_image"]["reference"], image_purpose
                )
                if result.get("persona_image") != turn["persona_image"]["reference"]:
                    raise MeetError("meet_persona_result_mismatch", 409)
            if not self.tasks.finish(turn, "completed"):
                raise MeetError("meet_turn_cancelled", 409)
            return result
        except Exception:
            self.tasks.finish(turn, "failed")
            raise

    def lease_allowed(self, task_id, lease_id):
        """Worker may observe current Hub authority, never amend or broaden it."""
        from agent.services.repository_registry import get_repository_registry
        from agent.services.source_control_access_policy import HubSourcePrincipal

        task = get_repository_registry().task_repo.get_by_id(task_id)
        if task is None or task.task_kind != "meet_media_turn" or task.status != "in_progress":
            return False
        context = (task.worker_execution_context or {}).get("meet_media", {})
        if "chat_reply" in context:
            # The v1 publisher does not implement dialog generation/key fencing.
            # A generated chat reply needs the separate MDS publication path.
            return False
        if (
            context.get("lease_id") != lease_id
            or context.get("deadline", 0) <= self.clock()
            or (task.tenant_id, task.project_id) not in self.allowed_scopes
        ):
            return False
        # Publication requires explicit project membership even for an admin who
        # can use the local preview. Role escalation is not part of this lease.
        principal = HubSourcePrincipal(
            context.get("owner_subject", ""), task.tenant_id, task.project_id, frozenset({"user"})
        )
        try:
            self.binding.require_write_access(principal, task.project_id, context.get("binding_task_id", ""))
            if "persona_image" in context:
                if self.persona_images is None:
                    return False
                self.persona_images.require_current(
                    principal, task.project_id, context["persona_image"], context["persona_purpose"]
                )
        except Exception:
            return False
        return True


class HubMediaTasks:
    """Existing Hub queue, content-free events and lease-fenced terminal CAS."""

    def start(self, turn, actor):
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=turn["task_id"],
            status="in_progress",
            title="Local Meet AI response",
            description="Hub-delegated local text, speech and synthetic avatar generation.",
            created_by=actor,
            source="meet_media",
            event_type="meet_media_delegated",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "meet_media_turn",
                "project_id": turn["project_id"],
                "tenant_id": turn["tenant_id"],
                "required_capabilities": ["meet_media_turn"],
                "parent_task_id": turn.get("binding_task_id"),
                "worker_execution_context": {
                    "meet_media": {
                        "lease_id": turn["lease_id"],
                        "deadline": turn["deadline"],
                        "owner_subject": actor,
                        "binding_task_id": turn.get("binding_task_id", ""),
                        **({"chat_reply": turn["hub_chat_binding"]} if "hub_chat_binding" in turn else {}),
                        **({"response_limits": turn["response_limits"]} if "response_limits" in turn else {}),
                        **(
                            {
                                "persona_image": turn["persona_image"]["reference"],
                                "persona_purpose": "publish" if "meeting" in turn else "preview",
                            }
                            if "persona_image" in turn
                            else {}
                        ),
                    }
                },
            },
        )

    def finish(self, turn, status):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        return compare_and_set_local_task_status(
            turn["task_id"],
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: (
                task.task_kind == "meet_media_turn"
                and task.tenant_id == turn["tenant_id"]
                and task.project_id == turn["project_id"]
                and (task.worker_execution_context or {}).get("meet_media", {}).get("lease_id") == turn["lease_id"]
            ),
            event_type=f"meet_media_{status}",
            event_actor="hub",
            event_details={"lease_id": turn["lease_id"]},
        )
