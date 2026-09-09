"""Hub-owned source/analysis admission, independent of rendering or persistence."""

import time
import uuid

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_source_job import source_job_current
from ananta_contracts.meet_visual_receive import PROFILE, validate_visual_result


class MeetDialogVisual:
    def __init__(self, authority, tasks, meet, *, clock=time.time):
        self.authority, self.tasks, self.meet, self.clock = authority, tasks, meet, clock

    @staticmethod
    def require_policy(scope):
        if (
            "video.receive" not in scope.capabilities
            or scope.controls.visual is None
            or not scope.controls.visual.enabled
        ):
            raise MeetError("meet_visual_policy_denied", 403)

    def current(self, ids, job):
        scope = self.authority.current(*ids)
        self.require_policy(scope)
        if job["control_revision"] != scope.controls.visual.revision or job["profile"] != PROFILE:
            raise MeetError("meet_visual_policy_changed", 403)
        self.tasks.require_job(scope, job)
        receipt = self.meet.inspect(*ids, job["meet_session_id"])
        if not source_job_current(job, receipt, self.clock()):
            raise MeetError("meet_visual_authority_changed", 409)
        return scope

    def start(self, payload):
        ids = tuple(payload[k] for k in ("task_id", "lease_id", "runtime_id"))
        scope = self.authority.current(*ids)
        self.require_policy(scope)
        receipt = self.meet.inspect(*ids, payload["meet_session_id"])
        publication = next(
            (p for p in receipt["publications"] if p["publicationId"] == payload["publication_id"]), None
        )
        if publication is None or publication["source"] not in ("camera", "screen"):
            raise MeetError("meet_visual_source_denied", 403)
        grant = next(g for g in receipt["grants"] if g["publisherPeerId"] == publication["peerId"])
        now = int(self.clock())
        deadline = min(now + 20, scope.deadline, receipt["lease"]["expiresAt"] // 1000, grant["expiresAt"] // 1000)
        if deadline < now + 10:
            raise MeetError("meet_visual_lease_too_short", 409)
        job = {
            "task_id": str(uuid.uuid4()),
            "lease_id": str(uuid.uuid4()),
            "issued_at": now,
            "deadline": deadline,
            "control_revision": scope.controls.visual.revision,
            "profile": PROFILE,
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
        try:
            self.tasks.claim(scope, job, now)
            self.current(ids, job)
        except Exception:
            self.tasks.finish(scope, job, "failed")
            raise
        return {"schema": "ananta.meet-visual-assignment.v1", "nonce": payload["nonce"], "job": job}

    def projection(self, scope, receipt):
        job = self.tasks.read(scope)["job"]
        if job is not None:
            try:
                self.require_policy(scope)
                self.tasks.require_job(scope, job, allow_completed=True)
                if job["control_revision"] != scope.controls.visual.revision or not source_job_current(
                    job, receipt, self.clock()
                ):
                    raise MeetError("meet_visual_authority_changed", 409)
            except MeetError:
                self.tasks.finish(scope, job, "failed")
                return None
        return job

    def complete(self, payload):
        from agent.common.task_mutation_lock import get_task_mutation_lock_port

        ids = tuple(payload[k] for k in ("task_id", "lease_id", "runtime_id"))
        scope = self.authority.current(*ids)
        job = self.tasks.read(scope)["job"]
        if job is None or (job["task_id"], job["lease_id"], job["meet_session_id"]) != (
            payload["visual_task_id"],
            payload["visual_lease_id"],
            payload["meet_session_id"],
        ):
            raise MeetError("meet_visual_assignment_mismatch", 403)
        try:
            validate_visual_result(payload["result"])
            # Share the Task repository's sorted, reentrant mutation locks with
            # control changes and cancellation. Re-read authority under both
            # locks; a pre-lock scope must never authorize the terminal write.
            with get_task_mutation_lock_port().mutation_locks({scope.task_id, job["task_id"]}) as acquired:
                if not acquired:
                    raise MeetError("meet_visual_completion_conflict", 409)
                scope = self.current(ids, job)
                if not self.tasks.finish(scope, job, "completed"):
                    raise MeetError("meet_visual_task_cancelled", 409)
            # Accept only this job's closed feature result; do not persist frame
            # contents/statistics or treat them as instructions or chat authority.
            return {"schema": "ananta.meet-visual-accepted.v1", "nonce": payload["nonce"]}
        except Exception:
            self.tasks.finish(scope, job, "failed")
            raise
