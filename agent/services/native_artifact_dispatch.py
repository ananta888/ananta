"""Authenticated Native dispatch binding from current Hub SQL authority.

Native dispatch already owns an execution lease and a registered Worker
assignment. It does not use the Recovery-child dispatch-token protocol.
The digest names that exact lease/assignment, while every use rechecks its
current expiration, grant signature and registration under caller row locks.
Heartbeat timestamps are intentionally excluded from this stable identity.
"""

from agent.ports.native_result_authority import NativeResultAuthorityPort
from ananta_contracts.native_context_bundle import native_context_digest


class NativeArtifactDispatch:
    def __init__(self, *, authority: NativeResultAuthorityPort, worker_id: str, worker_url: str):
        # These identities must come from strict registered-worker auth, never
        # from the transfer payload or a Worker-supplied task projection.
        if not worker_id or not worker_url:
            raise ValueError("native_artifact_authenticated_worker_required")
        self._authority, self._worker_id, self._worker_url = authority, worker_id, worker_url

    def require_current_digest(self, *, session, task, command, now):
        authority = self._authority.require_current(session=session, task=task, command=command, now=now)
        if authority.worker_id != self._worker_id or authority.worker_url != self._worker_url:
            raise ValueError("native_artifact_worker_assignment_mismatch")
        return native_dispatch_binding_digest(command=command, authority=authority)


def native_dispatch_binding_digest(*, command, authority):
    """Content digest of the Hub-issued dispatch identity, not an evidence ID."""
    return native_context_digest({
        "schema": "ananta.native-dispatch-binding.v1", "command_digest": native_context_digest(command.to_dict()),
        "assignment_id": authority.assignment_id, "assignment_revision": authority.assignment_revision,
        "worker_id": authority.worker_id, "worker_url": authority.worker_url,
        "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
    })
