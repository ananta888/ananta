"""Bounded Hub maintenance execution; no file discovery or independent worker loop."""

import time
from typing import Protocol

from agent.services.persona_asset_errors import PersonaStorageRetryableError
from agent.services.persona_retention_ports import (
    RetentionClaimStorePort,
    RetentionErasurePort,
    RetentionPolicyPort,
    RetiredAssetPort,
)
from agent.services.persona_retention_service import asset_digest, automation_principal
from agent.services.project_access_authority import ProjectAccessError


class RetentionTaskPort(Protocol):
    def start(self, record): ...
    def require(self, record): ...
    def finish(self, record, state) -> bool: ...


class PersonaRetentionRunner:
    def __init__(
        self,
        *,
        policy: RetentionPolicyPort,
        catalog: RetiredAssetPort,
        store: RetentionClaimStorePort,
        erasure: RetentionErasurePort,
        tasks: RetentionTaskPort,
        clock=time.time,
        monotonic=time.monotonic,
    ):
        self.policy, self.catalog, self.store, self.erasure, self.tasks = policy, catalog, store, erasure, tasks
        self.clock, self.monotonic = clock, monotonic

    def run_once(self, *, limit=5, stopped=lambda: False):
        summary = dict(claimed=0, completed=0, blocked=0, retrying=0, lost=0)
        deadline = self.monotonic() + 5
        for observed in self.store.due(int(self.clock() * 1000), limit=limit):
            if stopped() or self.monotonic() >= deadline:
                break
            # A process crash also consumes an attempt; never retry forever.
            if observed["attempts"] >= 5:
                self.tasks.finish(observed, "failed")
                if self.store.finish(observed, "blocked", int(self.clock() * 1000)):
                    summary["blocked"] += 1
                continue
            record = self.store.claim(observed, int(self.clock() * 1000))
            if record is None:
                continue
            summary["claimed"] += 1
            result = self._execute(record, observed, stopped)
            summary[result] += 1
        return summary

    def _execute(self, record, observed, stopped):
        principal = automation_principal(record["tenant_id"], record["project_id"], record["actor"])
        project, artifact = record["project_id"], record["artifact_id"]

        def checkpoint():
            if stopped():
                raise PermissionError("persona_retention_stopped")
            self.store.require_claim(record, int(self.clock() * 1000))
            self.policy.require_revoke(principal, project, artifact)
            self.tasks.require(record)

        started = False
        try:
            if observed["task_id"]:
                self.tasks.finish(observed, "failed")
            self.tasks.start(record)
            started = True
            checkpoint()
            asset, revision, state = self.catalog.get_retired(record["tenant_id"], project, artifact)
            if (
                asset_digest(asset) != record["asset_digest"]
                or not record["asset_revision"] <= revision <= record["asset_revision"] + 2
                or (revision != record["asset_revision"] and state not in ("purging", "purged"))
            ):
                raise ValueError("persona_retention_asset_changed")
            self.erasure.purge(principal, project, artifact, expected_revision=revision, require_current=checkpoint)
            checkpoint()
            if not self.tasks.finish(record, "completed"):
                raise PermissionError("persona_retention_task_changed")
            return "completed" if self.store.finish(record, "completed", int(self.clock() * 1000)) else "lost"
        except Exception as error:
            if started:
                self.tasks.finish(record, "failed")
            # Changed policy, corruption and unknown file layouts need a fresh
            # explicit grant. Transient execution failures may retry at most 5x.
            blocked = (
                isinstance(error, (PermissionError, ProjectAccessError, ValueError))
                and not isinstance(error, PersonaStorageRetryableError)
            ) or record["attempts"] >= 5
            state = "blocked" if blocked else "scheduled"
            if not self.store.finish(record, state, int(self.clock() * 1000)):
                return "lost"
            return "blocked" if blocked else "retrying"
