"""Compose current Hub/Meet authority, bounded dialog tasks and Worker dispatch."""

import json
import time
import uuid

from agent.services.meet_chat_admission import MeetChatAdmissionService
from agent.services.meet_chat_contract import ChatEvent
from agent.services.meet_chat_policy import ChatReplyPolicy
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_chat_authority import CurrentDialogChatAuthority
from agent.services.meet_dialog_controls import (
    change_controls,
    controls_projection,
    initial_controls,
)
from agent.services.meet_dialog_replies import MeetDialogReplies
from agent.services.meet_dialog_spoken_reply import MeetDialogSpokenReply
from agent.services.meet_turn_service import HubMediaTasks
from ananta_contracts.meet_source_profile import dialog_source_profile


class MeetDialogService:
    def __init__(
        self,
        authority,
        tasks,
        meet,
        issuer,
        worker,
        media_worker,
        reservations,
        dispatches,
        clock=time.time,
        media_tasks=None,
        replies=None,
        avatar_profiles=None,
        voice_profiles=None,
        phases=None,
    ):
        self.authority, self.tasks, self.meet, self.issuer = authority, tasks, meet, issuer
        self.phases = phases
        self.worker, self.media_worker, self.reservations, self.dispatches, self.clock = (
            worker,
            media_worker,
            reservations,
            dispatches,
            clock,
        )
        self.media_tasks = media_tasks if media_tasks is not None else HubMediaTasks()
        self.replies = (
            replies
            if replies is not None
            else MeetDialogReplies(
                authority.binding,
                media_worker,
                self.media_tasks,
                dispatches,
                clock=clock,
            )
        )
        from agent.services.meet_dialog_audio import MeetDialogAudio

        self.audio_coordinator = MeetDialogAudio(
            authority,
            tasks,
            meet,
            reservations,
            dispatches,
            media_worker,
            self.media_tasks,
            clock,
            replies=self.replies,
        )
        from agent.services.meet_dialog_voice_selection import MeetDialogVoiceSelection
        from agent.services.meet_dialog_voices import MeetDialogVoices

        self.voices = MeetDialogVoices(voice_profiles, self.replies.speech_profile, clock=clock)
        self.voice_selections = MeetDialogVoiceSelection(authority, tasks, voice_profiles, clock=clock)
        self.spoken_replies = MeetDialogSpokenReply(
            authority, meet, reservations, self.replies, clock=clock, voices=self.voices
        )
        from agent.services.meet_dialog_avatar_images import MeetDialogAvatarImages
        from agent.services.meet_dialog_avatar_selection import MeetDialogAvatarSelection

        self.avatar_images = MeetDialogAvatarImages(authority, meet, avatar_profiles, clock=clock)
        self.avatar_selections = MeetDialogAvatarSelection(authority, tasks, avatar_profiles, clock=clock)

    def spoken_reply(self, payload):
        return self.spoken_replies.execute(payload)

    def list(self, principal, project, cursor=0):
        self.authority.binding.require_write_access(principal, project)
        if type(cursor) is not int or not 0 <= cursor <= 100000:
            raise MeetError("meet_dialog_cursor_invalid")
        rows = self.tasks.list_page(principal.tenant_id, project, cursor)
        items = []
        for task in rows:
            context = (task.worker_execution_context or {}).get("meet_dialog", {})
            if (
                task.tenant_id == principal.tenant_id
                and task.project_id == project
                and context.get("owner_subject") == principal.subject_id
            ):
                items.append(self.inspect(principal, project, task.id))
        return {
            "schema": "ananta.meet-dialog-list.v1",
            "items": items,
            "next_cursor": cursor + 50 if len(rows) == 50 and cursor < 100000 else None,
        }

    def start(self, principal, project, payload, parent=""):
        self.authority.binding.require_write_access(principal, project, parent)
        if (
            not isinstance(payload, dict)
            or set(payload) - {"audio_mode", "avatar_images", "voice_profiles"}
            != {"capabilities", "duration_seconds", "chat_mode"}
            or type(payload["duration_seconds"]) is not int
            or not 30 <= payload["duration_seconds"] <= 7200
            or not isinstance(payload["capabilities"], list)
            or not payload["capabilities"]
            or any(not isinstance(v, str) for v in payload["capabilities"])
            or len(set(payload["capabilities"])) != len(payload["capabilities"])
            or not set(payload["capabilities"])
            <= self.authority.policies.get((principal.tenant_id, project), frozenset())
        ):
            raise MeetError("meet_dialog_start_denied", 403)
        if "avatar_images" in payload and (
            payload["avatar_images"] is not True or "avatar.publish" not in payload["capabilities"]
        ):
            raise MeetError("meet_dialog_avatar_images_invalid", 403)
        if payload.get("avatar_images") is True and self.avatar_images.profiles is None:
            raise MeetError("meet_dialog_avatar_profiles_unavailable", 409)
        if "voice_profiles" in payload and (
            payload["voice_profiles"] is not True or "speech.publish" not in payload["capabilities"]
        ):
            raise MeetError("meet_dialog_voice_profiles_invalid", 403)
        if payload.get("voice_profiles") is True and (
            self.voices.profiles is None or self.voices.configured_profile is None
        ):
            raise MeetError("meet_dialog_voice_profiles_unavailable", 409)
        ChatReplyPolicy(mode=payload["chat_mode"])
        audio_mode = payload.get("audio_mode", "off")
        if (
            not isinstance(audio_mode, str)
            or audio_mode not in {"off", "transcribe", "dialog"}
            or audio_mode != "off"
            and not {"audio.receive", "chat.send"} <= set(payload["capabilities"])
            or audio_mode == "dialog"
            and payload["chat_mode"] == "off"
        ):
            raise MeetError("meet_dialog_audio_policy_denied", 403)
        if (
            payload["chat_mode"] != "off"
            and audio_mode != "dialog"
            and not {"chat.read", "chat.send"} <= set(payload["capabilities"])
        ):
            raise MeetError("meet_dialog_chat_rights_required", 403)
        stored = self.authority.binding.read(principal, project, parent)
        if not stored["invite_url"]:
            raise MeetError("meet_room_binding_required", 409)
        context = {
            "lease_id": str(uuid.uuid4()),
            "runtime_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "room_id": self.authority.binding.profile.parse_invite(stored["invite_url"]),
            "owner_subject": principal.subject_id,
            "binding_task_id": parent,
            "deadline": int(self.clock()) + payload["duration_seconds"],
            "capabilities": sorted(payload["capabilities"]),
            "chat_mode": payload["chat_mode"],
            "audio_mode": audio_mode,
            "audio_job": None,
            "audio_count": 0,
        }
        context["controls"] = initial_controls(
            context["capabilities"], context["chat_mode"], audio_mode, int(self.clock() * 1000)
        )
        if payload.get("avatar_images") is True:
            context["avatar_selection"] = {"mode": "neutral-ai-v1"}
        if payload.get("voice_profiles") is True:
            context["voice_selection"] = {"mode": "configured-piper-v1"}
        context["source_profile"] = dialog_source_profile(
            context["capabilities"], avatar_images="avatar_selection" in context
        ).projection()
        task_id = str(uuid.uuid4())
        authorization = {}
        if self.authority.preauthorization is not None:
            authorization["preauthorization"] = self.authority.preauthorization.reserve(
                task_id, principal.tenant_id, project, self.authority.binding.profile.origin, context
            )
        phase = (
            {} if self.phases is None else {"phase": self.phases.queued(task_id, principal.tenant_id, project, context)}
        )
        self.tasks.start(task_id, principal.tenant_id, project, context, **phase, **authorization)
        try:
            scope = self.authority.current(task_id, context["lease_id"], context["runtime_id"])
            if self.phases is not None:
                self.phases.advance(scope, "admitted")
                self.phases.advance(scope, "connecting")
            meeting = self.issuer.issue_dialog(self.authority, task_id, scope.lease_id, scope.runtime_id, self.clock())
            assignment = {
                "schema": "ananta.meet-dialog-assignment.v1",
                "task_id": task_id,
                "lease_id": scope.lease_id,
                "runtime_id": scope.runtime_id,
                "session_id": scope.session_id,
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "deadline": scope.deadline,
                "capabilities": list(scope.capabilities),
                "audio_mode": scope.audio_mode,
                "meeting": meeting,
            }
            if "avatar_selection" in context:
                assignment["avatar_images"] = True
            if "voice_selection" in context:
                assignment["voice_profiles"] = True
            self.worker.start_dialog(assignment)
            self.authority.current(task_id, scope.lease_id, scope.runtime_id)
            return {
                "schema": "ananta.meet-dialog-start.v1",
                "task_id": task_id,
                "session_id": scope.session_id,
                "status": "connecting",
            }
        except Exception:
            self.tasks.finish_bound(task_id, context["lease_id"], context["runtime_id"], "failed")
            raise

    def inspect(self, principal, project, task_id, *, stop=False):
        self.authority.binding.require_write_access(principal, project)
        task = self.tasks.get_by_id(task_id)
        if (
            task is None
            or task.task_kind != "meet_dialog_session"
            or task.tenant_id != principal.tenant_id
            or task.project_id != project
        ):
            raise MeetError("meet_dialog_not_found", 404)
        context = (task.worker_execution_context or {}).get("meet_dialog", {})
        if context.get("owner_subject") != principal.subject_id:
            raise MeetError("meet_dialog_owner_required", 403)
        if stop and task.status == "in_progress":
            try:
                if self.phases is not None:
                    self.phases.stopping(task_id, context["lease_id"], context["runtime_id"])
            finally:
                # Observational metadata must never obstruct bound cancellation.
                self.tasks.finish_bound(task_id, context["lease_id"], context["runtime_id"], "cancelled")
            task = self.tasks.get_by_id(task_id)
        result = {
            "schema": "ananta.meet-dialog-status.v1",
            "task_id": task_id,
            "status": task.status,
            "deadline": context.get("deadline", 0),
            "controls": context.get("controls"),
            "capabilities": context.get("capabilities", []),
        }
        if "avatar_selection" in context:
            result["avatar_selection"] = context["avatar_selection"]
        if "voice_selection" in context:
            result["voice_selection"] = context["voice_selection"]
        return result

    def select_avatar(self, principal, project, task_id, payload):
        self.inspect(principal, project, task_id)
        context = self.tasks.get_by_id(task_id).worker_execution_context["meet_dialog"]
        scope = self.authority.current(task_id, context["lease_id"], context["runtime_id"])
        self.avatar_selections.select(principal, scope, payload)
        return self.inspect(principal, project, task_id)

    def avatar_image(self, payload):
        return self.avatar_images.hydrate(payload)

    def select_voice(self, principal, project, task_id, payload):
        self.inspect(principal, project, task_id)
        context = self.tasks.get_by_id(task_id).worker_execution_context["meet_dialog"]
        scope = self.authority.current(task_id, context["lease_id"], context["runtime_id"])
        self.voice_selections.select(principal, scope, payload)
        return self.inspect(principal, project, task_id)

    def control(self, principal, project, task_id, payload):
        self.inspect(principal, project, task_id)
        task = self.tasks.get_by_id(task_id)
        context = task.worker_execution_context["meet_dialog"]
        scope = self.authority.current(task_id, context["lease_id"], context["runtime_id"])
        controls = change_controls(scope, payload, int(self.clock() * 1000))
        if not self.tasks.set_controls(scope, controls):
            raise MeetError("meet_dialog_controls_conflict", 409)
        return self.inspect(principal, project, task_id)

    def exchange(self, payload):
        from agent.services.meet_dialog_audio import audio_job_current

        ids = (payload["task_id"], payload["lease_id"], payload["runtime_id"])
        scope = self.authority.current(*ids)
        state = self.meet.inspect(*ids, payload["meet_session_id"])
        scope = self.authority.current(*ids)
        if self.phases is not None:
            self.phases.advance(scope, "joined", state)
        task = self.tasks.get_by_id(scope.task_id)
        audio_job = (task.worker_execution_context or {}).get("meet_dialog", {}).get("audio_job")
        if audio_job and (
            not scope.controls.audio.enabled
            or audio_job["control_revision"] != scope.controls.audio.revision
            or not audio_job_current(audio_job, state, self.clock())
        ):
            self.tasks.finish_audio(scope, audio_job, "failed")
            audio_job = None
        renewal = None
        if state["lease"]["expiresAt"] < min((self.clock() + 60) * 1000, scope.deadline * 1000):
            renewal = self.issuer.issue_dialog(self.authority, *ids, self.clock())["grant"]
        result = {
            "schema": "ananta.meet-dialog-state.v1",
            "nonce": payload["nonce"],
            "authorization": state,
            "renewal": renewal,
            "audio_job": audio_job,
            "controls": controls_projection(scope.controls),
        }
        if scope.avatar_selection is not None:
            result["avatar"] = self.avatar_images.projection(scope, state)
        if scope.voice_selection is not None:
            result["voice"] = self.voices.projection(scope)
        return result

    def audio(self, payload):
        return self.audio_coordinator.start(payload)

    def transcript(self, payload):
        return self.audio_coordinator.complete(payload)

    def chat(self, payload):
        from agent.services.source_control_access_policy import HubSourcePrincipal

        ids = (payload["task_id"], payload["lease_id"], payload["runtime_id"])
        scope = self.authority.current(*ids)
        raw = json.dumps(payload["event"], ensure_ascii=False).encode()
        event = ChatEvent.parse(raw)
        if event.sent_at_ms < scope.controls.chat.since:
            return {
                "schema": "ananta.meet-dialog-answer.v1",
                "nonce": payload["nonce"],
                "code": "control_changed",
                "reply": None,
            }
        current = CurrentDialogChatAuthority(
            self.authority, self.meet, ids, payload["meet_session_id"], event.sender_peer_id
        )
        admission = MeetChatAdmissionService(current, self.reservations, clock=self.clock).admit(raw)
        if admission.reservation is None:
            return {
                "schema": "ananta.meet-dialog-answer.v1",
                "nonce": payload["nonce"],
                "code": admission.code,
                "reply": None,
            }
        principal = HubSourcePrincipal(scope.owner_subject, scope.tenant_id, scope.project_id, frozenset({"user"}))
        result = self.replies.execute(current, principal, admission)
        return {
            "schema": "ananta.meet-dialog-answer.v1",
            "nonce": payload["nonce"],
            "code": "generated",
            "reply": {"message_id": result["reply_to"], "text": result["media"]["text"]},
        }

    def finish(self, payload):
        self.tasks.finish_bound(payload["task_id"], payload["lease_id"], payload["runtime_id"], payload["status"])
        return {"schema": "ananta.meet-dialog-finished.v1", "nonce": payload["nonce"]}
