"""Hub policy and task lifecycle for bounded local chat/media responses."""

import time
import uuid
from typing import Protocol

from agent.services.meet_capacity_admission import MediaCapacityPort
from agent.services.meet_contract import MeetError
from agent.services.meet_speech_result import validate_speech_binding
from agent.services.meet_visual_selection import MeetVisualSelections
from ananta_contracts.meet_speech import validate_speech_profile
from worker.meet_media.contract import SCHEMA, validate_turn


class MediaWorkerPort(Protocol):
    def execute(self, turn: dict) -> dict: ...


class MediaTaskPort(Protocol):
    def start(self, turn: dict, actor: str) -> None: ...
    def finish(self, turn: dict, status: str) -> bool: ...


class PersonaImagePort(Protocol):
    def prepare(self, principal, project: str, artifact_id: str, purpose: str) -> dict: ...
    def require_current(self, principal, project: str, reference: dict, purpose: str) -> None: ...


class PersonaProfilePort(Protocol):
    def prepare(self, principal, project: str, selection: dict, purpose: str) -> tuple[dict, dict]: ...
    def require_current(self, principal, project: str, binding: dict, reference: dict) -> None: ...


class PersonaVideoPort(Protocol):
    def prepare(self, principal, project: str, artifact_id: str, purpose: str, *, repeat_mode: str) -> dict: ...
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
        persona_profiles: PersonaProfilePort | None = None,
        speech_profile=None,
        capacity: MediaCapacityPort | None = None,
        persona_videos: PersonaVideoPort | None = None,
    ):
        self.binding, self.worker, self.tasks = binding, worker, tasks
        self.allowed_scopes = frozenset(allowed_scopes)
        self.clock = clock
        self.grant_issuer = grant_issuer
        self.persona_images = persona_images
        self.persona_profiles = persona_profiles
        self.persona_videos = persona_videos
        self.speech_profile = validate_speech_profile(speech_profile) if speech_profile is not None else None
        self.capacity = capacity

    def _visuals(self):
        return MeetVisualSelections(
            images=self.persona_images, image_profiles=self.persona_profiles, videos=self.persona_videos
        )

    def execute(self, principal, project, payload, task=""):
        # This is generation authority, not authority to join or publish in Meet.
        self.binding.require_write_access(principal, project, task)
        if (principal.tenant_id, project) not in self.allowed_scopes:
            raise MeetError("meet_media_policy_denied", 403)
        selections = self._visuals()
        selections.validate_payload(payload)
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
        if self.speech_profile is not None:
            turn["speech_profile"] = dict(self.speech_profile)
        visual = selections.prepare(principal, project, payload)
        if visual is not None:
            turn.update(visual.worker_fields)
        if payload.get("publish_to_meet"):
            if self.grant_issuer is None:
                raise MeetError("meet_machine_publication_disabled", 403)
            turn["meeting"] = self.grant_issuer.issue(turn, self.binding, principal, self.clock(), task=task)
        try:
            validate_turn(turn, self.clock())
        except ValueError as exc:
            raise MeetError(str(exc)) from None
        # Only closed admitted asset bytes/reference go to the worker; profile
        # ancestry and configuration pins remain in the authoritative Hub task.
        hub_turn = turn | (visual.hub_fields if visual is not None else {})
        self.tasks.start(hub_turn, principal.subject_id)
        try:

            def require_dispatch():
                self.binding.require_write_access(principal, project, task)
                if visual is not None:
                    visual.require_current()

            require_dispatch()
            result = (
                self.capacity.run(hub_turn, lambda: self.worker.execute(turn), require_dispatch)
                if self.capacity is not None
                else self.worker.execute(turn)
            )
            validate_speech_binding(turn, result)
            if (
                self.clock() >= turn["deadline"]
                or result.get("task_id") != turn["task_id"]
                or result.get("lease_id") != turn["lease_id"]
            ):
                raise MeetError("meet_turn_result_stale", 409)
            # Recheck project access before disclosing generated media.
            require_dispatch()
            if visual is not None:
                visual.require_result(result)
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
            or context.get("speech_profile") != self.speech_profile
        ):
            return False
        # Publication requires explicit project membership even for an admin who
        # can use the local preview. Role escalation is not part of this lease.
        principal = HubSourcePrincipal(
            context.get("owner_subject", ""), task.tenant_id, task.project_id, frozenset({"user"})
        )
        try:
            self.binding.require_write_access(principal, task.project_id, context.get("binding_task_id", ""))
            self._visuals().require_context(principal, task.project_id, context)
        except Exception:
            return False
        return True


class HubMediaTasks:
    """Existing Hub queue, content-free events and lease-fenced terminal CAS."""

    def require_current(self, turn):
        from agent.services.repository_registry import get_repository_registry

        task = get_repository_registry().task_repo.get_by_id(turn["task_id"])
        context = (task.worker_execution_context or {}).get("meet_media", {}) if task is not None else {}
        expected = {
            "lease_id": turn["lease_id"],
            "deadline": turn["deadline"],
            "binding_task_id": turn.get("binding_task_id", ""),
            "speech_profile": turn.get("speech_profile"),
            "persona_profile": turn.get("hub_persona_profile"),
            "persona_image": turn.get("persona_image", {}).get("reference"),
            "persona_video": turn.get("persona_video", {}).get("reference"),
            "persona_video_repeat_mode": turn.get("persona_video", {}).get("repeat_mode"),
            "persona_purpose": ("publish" if "meeting" in turn else "preview")
            if {"persona_image", "persona_video"} & set(turn)
            else None,
            "chat_reply": turn.get("hub_chat_binding"),
            "response_limits": turn.get("response_limits"),
        }
        if (
            task is None
            or task.task_kind != "meet_media_turn"
            or task.status != "in_progress"
            or task.tenant_id != turn["tenant_id"]
            or task.project_id != turn["project_id"]
            or any(context.get(key) != value for key, value in expected.items())
        ):
            raise MeetError("meet_capacity_task_changed", 409)

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
                        **({"speech_profile": turn["speech_profile"]} if "speech_profile" in turn else {}),
                        **({"persona_profile": turn["hub_persona_profile"]} if "hub_persona_profile" in turn else {}),
                        **(
                            {
                                "persona_image": turn["persona_image"]["reference"],
                                "persona_purpose": "publish" if "meeting" in turn else "preview",
                            }
                            if "persona_image" in turn
                            else {}
                        ),
                        **(
                            {
                                "persona_video": turn["persona_video"]["reference"],
                                "persona_video_repeat_mode": turn["persona_video"]["repeat_mode"],
                                "persona_purpose": "publish" if "meeting" in turn else "preview",
                            }
                            if "persona_video" in turn
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
                and (task.worker_execution_context or {}).get("meet_media", {}).get("speech_profile")
                == turn.get("speech_profile")
            ),
            event_type=f"meet_media_{status}",
            event_actor="hub",
            event_details={"lease_id": turn["lease_id"]},
        )
