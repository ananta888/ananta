"""Hub navigation/task ownership and independent sanitized-view presentation."""

import time
import uuid

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_browser_workspace import validate_browser_job, validate_browser_source


class MeetBrowserWorkspaces:
    def __init__(self, authority, tasks, policy, *, clock=time.time, identity=lambda: str(uuid.uuid4())):
        self.authority, self.tasks, self.policy, self.clock, self.identity = authority, tasks, policy, clock, identity

    def owner_scope(self, principal, project, task_id):
        self.authority.binding.require_write_access(principal, project)
        parent = self.tasks.get_parent(task_id)
        if (
            parent is None
            or parent.task_kind != "meet_dialog_session"
            or (parent.tenant_id, parent.project_id) != (principal.tenant_id, project)
        ):
            raise MeetError("meet_browser_not_found", 404)
        context = (parent.worker_execution_context or {}).get("meet_dialog", {})
        if context.get("owner_subject") != principal.subject_id:
            raise MeetError("meet_browser_owner_required", 403)
        scope = self.authority.current(task_id, context.get("lease_id"), context.get("runtime_id"))
        if not scope.browser_workspace:
            raise MeetError("meet_browser_not_negotiated", 403)
        return scope

    def inspect(self, principal, project, task_id):
        scope = self.owner_scope(principal, project, task_id)
        state = self.tasks.read(scope)
        job = state["job"]
        # This owner-only receipt has no document, URL, worker endpoint or grant.
        return {
            "schema": "ananta.meet-browser-status.v1",
            "revision": state["revision"],
            "mode": state["mode"],
            "task_id": job["task_id"] if job else None,
            "deadline_ms": job["deadline_ms"] if job else None,
        }

    def change(self, principal, project, task_id, payload):
        scope = self.owner_scope(principal, project, task_id)
        if type(payload) is not dict or payload.get("action") not in ("navigate", "present", "stop", "status"):
            raise MeetError("meet_browser_control_invalid")
        action = payload["action"]
        fields = {"action", "expected_revision"} | ({"url"} if action == "navigate" else set())
        if set(payload) != fields or type(payload["expected_revision"]) is not int:
            raise MeetError("meet_browser_control_invalid")
        old = self.tasks.read(scope)
        if payload["expected_revision"] != old["revision"] or old["revision"] >= 1023:
            raise MeetError("meet_browser_control_conflict", 409)
        changed = old | {"revision": old["revision"] + 1}
        if action == "navigate":
            job = self._job(scope, payload["url"], changed["revision"])
            changed |= {"mode": "off", "job": job}  # Navigation never enables presentation.
        elif action == "present":
            job = old["job"]
            if job is None:
                raise MeetError("meet_browser_task_required", 409)
            self._require_job(scope, job, present=True)
            changed["mode"] = "browser"
        else:
            changed |= {"mode": "status" if action == "status" else "off", "job": None}
        if self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id) != scope:
            raise MeetError("meet_browser_authority_changed", 409)
        if not self.tasks.replace(scope, old, changed):
            raise MeetError("meet_browser_control_conflict", 409)
        if action != "present" and old["job"] is not None:
            self.tasks.finish(scope, old["job"], "cancelled")
        if action == "navigate":
            try:
                self.tasks.create(scope, job)
                current = self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id)
                self._require_job(current, job)
            except Exception:
                self.tasks.finish(scope, job, "failed")
                raise MeetError("meet_browser_dispatch_failed", 409) from None
        return self.inspect(principal, project, task_id)

    def _job(self, scope, url, revision):
        policy, request = self.policy.navigation(scope, url)
        now = int(self.clock() * 1000)
        deadline = min(now + 30000, scope.deadline * 1000)
        if deadline - now < 5000:
            raise MeetError("meet_browser_parent_lease_too_short", 409)
        return validate_browser_job(
            {
                "schema": "ananta.meet-browser-job.v1",
                **{name: self.identity() for name in ("task_id", "lease_id", "workspace_id", "page_id")},
                "parent_task_id": scope.task_id,
                "parent_lease_id": scope.lease_id,
                **{name: getattr(scope, name) for name in ("tenant_id", "project_id", "runtime_id", "session_id")},
                "policy_id": policy.policy_id,
                "policy_revision": policy.revision,
                "fetch": request,
                "navigation_revision": revision,
                "issued_at_ms": now,
                "deadline_ms": deadline,
            }
        )

    def _require_job(self, scope, job, *, present=False):
        self.tasks.require_job(scope, job)
        self.policy.current_job(scope, job, present=present)
        if int(self.clock() * 1000) >= job["deadline_ms"]:
            raise MeetError("meet_browser_expired", 403)

    def projection(self, scope, receipt):
        state = self.tasks.read(scope)
        result = {
            "schema": "ananta.meet-browser-source.v1",
            "revision": state["revision"],
            "mode": state["mode"],
            "job": None,
            "binding": None,
            "reason": "not_selected",
        }
        job = state["job"]
        if job is None:
            return validate_browser_source(result)
        try:
            self._require_job(scope, job, present=state["mode"] == "browser")
            if receipt["roomId"] != scope.room_id:
                raise MeetError("meet_browser_parent_mismatch", 403)
            deadline = min(job["deadline_ms"], receipt["lease"]["expiresAt"], scope.deadline * 1000)
            if int(self.clock() * 1000) >= deadline:
                raise MeetError("meet_browser_expired", 403)
            result |= {
                "job": job,
                "reason": "ready",
                "binding": {
                    "meet_session_id": receipt["lease"]["sessionId"],
                    "own_peer_id": receipt["peerId"],
                    "generation": receipt["lease"]["generation"],
                    "membership_epoch": receipt["membershipEpoch"],
                    "screen_revision": scope.controls.screen.revision,
                    "deadline_ms": deadline,
                },
            }
            return validate_browser_source(result)
        except (ValueError, KeyError, TypeError) as error:
            reason = (
                "expired" if isinstance(error, MeetError) and error.code == "meet_browser_expired" else "task_inactive"
            )
            if isinstance(error, MeetError) and error.code in {
                "meet_browser_policy_denied",
                "meet_browser_navigation_denied",
            }:
                reason = "policy_denied"
            self.tasks.finish(scope, job, "timeout" if reason == "expired" else "cancelled")
            return validate_browser_source(result | {"mode": "off", "job": None, "binding": None, "reason": reason})
