"""Hub-owned utterance admission. Browser samples and ASR stay on the Worker."""

import json
import re
import time
import uuid

from agent.services.meet_chat_admission import AuthorizedChatSession, MeetChatAdmissionService
from agent.services.meet_chat_contract import ChatScope
from agent.services.meet_chat_policy import ChatReplyPolicy
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import chat_policy_revision
from agent.services.meet_dialog_lifecycle import organization_tuple
from agent.services.meet_dialog_replies import MeetDialogReplies
from ananta_contracts.meet_audio_policy import audio_mode_permitted
from ananta_contracts.meet_audio_profile import optional_audio_profile
from ananta_contracts.meet_dialog_audio import audio_job_current


class AudioReplyAuthority:
    def __init__(self, coordinator, ids, job):
        self.coordinator, self.ids, self.job = coordinator, ids, job

    def current(self, session_id):
        scope, receipt = self.coordinator.current(self.ids, self.job)
        if scope.session_id != session_id or scope.audio_mode != "dialog":
            return None
        return AuthorizedChatSession(
            ChatScope(
                origin=scope.origin,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                task_id=scope.task_id,
                session_id=scope.session_id,
                runtime_id=scope.runtime_id,
                lease_id=scope.lease_id,
                generation=self.job["generation"],
                room_id=scope.room_id,
                membership_epoch=self.job["membership_epoch"],
                policy_revision=chat_policy_revision(self.job["receive_revision"], self.job["control_revision"]),
                own_peer_id=receipt["peerId"],
                deadline_ms=self.job["deadline"] * 1000,
            ),
            ChatReplyPolicy(mode=scope.chat_mode),
        )


class MeetDialogAudio:
    def __init__(
        self, authority, tasks, meet, reservations, dispatches, media_worker, media_tasks, clock=time.time, replies=None
    ):
        self.authority, self.tasks, self.meet, self.clock = authority, tasks, meet, clock
        self.reservations, self.dispatches, self.media_worker, self.media_tasks = (
            reservations,
            dispatches,
            media_worker,
            media_tasks,
        )
        self.replies = (
            replies
            if replies is not None
            else MeetDialogReplies(
                authority.binding,
                media_worker,
                media_tasks,
                dispatches,
                clock=clock,
            )
        )

    def start(self, payload):
        ids = tuple(payload[k] for k in ("task_id", "lease_id", "runtime_id"))
        scope = self.authority.current(*ids)
        if (
            scope.audio_mode == "off"
            or not scope.controls.audio.enabled
            or not audio_mode_permitted(scope.audio_mode, scope.capabilities)
            or scope.audio_mode == "dialog"
            and scope.chat_mode == "off"
        ):
            raise MeetError("meet_audio_policy_denied", 403)
        receipt = self.meet.inspect(*ids, payload["meet_session_id"])
        publication = next(
            (p for p in receipt["publications"] if p["publicationId"] == payload["publication_id"]), None
        )
        if publication is None:
            raise MeetError("meet_audio_source_denied", 403)
        grant = next(g for g in receipt["grants"] if g["publisherPeerId"] == publication["peerId"])
        now = int(self.clock())
        deadline = min(now + 30, scope.deadline, receipt["lease"]["expiresAt"] // 1000, grant["expiresAt"] // 1000)
        if deadline < now + 15:
            raise MeetError("meet_audio_lease_too_short", 409)
        job = {
            "task_id": str(uuid.uuid4()),
            "lease_id": str(uuid.uuid4()),
            "issued_at": now,
            "deadline": deadline,
            "control_revision": scope.controls.audio.revision,
            "meet_session_id": payload["meet_session_id"],
            "generation": receipt["lease"]["generation"],
            "membership_epoch": receipt["membershipEpoch"],
            "receive_revision": receipt["receiveRevision"],
            "peer_id": publication["peerId"],
            "own_peer_id": receipt["peerId"],
            "publication_id": publication["publicationId"],
            "publication_epoch": publication["publicationEpoch"],
            "source": publication["source"],
        }
        if scope.audio_profile is not None:
            job["audio_profile"] = scope.audio_profile.projection()
        self.tasks.claim_audio(scope, job, now)
        self.current(ids, job)
        return {"schema": "ananta.meet-audio-assignment.v1", "nonce": payload["nonce"], "job": job}

    def current(self, ids, job):
        scope = self.authority.current(*ids)
        if (
            scope.audio_mode == "off"
            or not scope.controls.audio.enabled
            or not audio_mode_permitted(scope.audio_mode, scope.capabilities)
            or scope.audio_mode == "dialog"
            and scope.chat_mode == "off"
            or job["control_revision"] != scope.controls.audio.revision
            or job.get("audio_profile")
            != (scope.audio_profile.projection() if scope.audio_profile is not None else None)
        ):
            raise MeetError("meet_audio_policy_denied", 403)
        parent = self.tasks.get_by_id(scope.task_id)
        child = self.tasks.get_by_id(job["task_id"])
        if (
            (parent.worker_execution_context or {}).get("meet_dialog", {}).get("audio_job") != job
            or child is None
            or child.task_kind != "meet_audio_receive"
            or child.status != "in_progress"
            or child.tenant_id != scope.tenant_id
            or child.project_id != scope.project_id
            or getattr(child, "parent_task_id", None) != scope.task_id
            or organization_tuple(child) != organization_tuple(parent)
            or (child.worker_execution_context or {})
            != {"meet_audio": job, "parent_dispatch": scope.lease_id, "runtime_id": scope.runtime_id}
        ):
            raise MeetError("meet_audio_task_inactive", 403)
        receipt = self.meet.inspect(*ids, job["meet_session_id"])
        if not audio_job_current(job, receipt, self.clock()):
            raise MeetError("meet_audio_authority_changed", 409)
        return scope, receipt

    def complete(self, payload):
        from agent.services.source_control_access_policy import HubSourcePrincipal

        ids = tuple(payload[k] for k in ("task_id", "lease_id", "runtime_id"))
        scope = self.authority.current(*ids)
        parent = self.tasks.get_by_id(scope.task_id)
        job = (parent.worker_execution_context or {}).get("meet_dialog", {}).get("audio_job")
        if not job or (job["task_id"], job["lease_id"]) != (payload["audio_task_id"], payload["audio_lease_id"]):
            raise MeetError("meet_audio_assignment_mismatch", 403)
        reply = None
        try:
            self.current(ids, job)
            profile = optional_audio_profile(job)
            if (
                type(payload["end_sample"]) is not int
                or payload["end_sample"] != profile.end_sample
                or payload["language"] not in {"de", "en"}
                or "audio_profile" in job
                and payload["language"] != profile.language
            ):
                raise MeetError("meet_audio_result_invalid")
            text = payload["text"]
            if not isinstance(text, str) or len(text) > 2000 or len(text.encode("utf-8")) > 4000:
                raise MeetError("meet_audio_result_invalid")
            if scope.audio_mode == "dialog" and text.strip():
                # A spoken vocative is not literally an '@' character. This only
                # normalizes addressing for reply eligibility; it grants no rights.
                addressed = re.sub(r"^\s*(?:(?:hey|hallo)\s+)?ananta\b", "@ananta", text, flags=re.IGNORECASE)
                # Separate source admission above; this projection reuses only the
                # bounded answer policy/reservation, not typed-chat read permission.
                event = {
                    "schema": "ananta.meet-chat-event.draft1",
                    "session_id": scope.session_id,
                    "generation": job["generation"],
                    "room_id": scope.room_id,
                    "membership_epoch": job["membership_epoch"],
                    "message_id": job["task_id"].replace("-", ""),
                    "sender_peer_id": job["peer_id"],
                    "sender_kind": "human",
                    "sent_at_ms": job["issued_at"] * 1000,
                    "text": addressed,
                }
                current = AudioReplyAuthority(self, ids, job)
                admission = MeetChatAdmissionService(current, self.reservations, clock=self.clock).admit(
                    json.dumps(event).encode()
                )
                if admission.reservation:
                    principal = HubSourcePrincipal(
                        scope.owner_subject, scope.tenant_id, scope.project_id, frozenset({"user"})
                    )
                    generated = self.replies.execute(current, principal, admission)
                    reply = {"text": generated["media"]["text"]}
            self.current(ids, job)
            if not self.tasks.finish_audio(scope, job, "completed", release=False):
                raise MeetError("meet_audio_task_cancelled", 409)
            return {"schema": "ananta.meet-audio-result.v1", "nonce": payload["nonce"], "reply": reply}
        except Exception:
            self.tasks.finish_audio(scope, job, "failed")
            raise
