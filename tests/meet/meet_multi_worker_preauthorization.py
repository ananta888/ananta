"""Explicit test-owned operator policy; no serving room, key or database access."""

import os
import time
from uuid import uuid4

from agent.repositories.meet_preauthorizations import SqlMeetPreauthorizations
from agent.services.meet_dialog_preauthorization import MeetDialogPreauthorization


class MultiWorkerPreauthorization:
    def __init__(self, engine, authority, parents, principal, room, *, duration_seconds):
        if type(duration_seconds) is not int or duration_seconds not in {120, 180}:
            raise ValueError("test_multi_policy_duration_invalid")
        self.store = SqlMeetPreauthorizations(engine)
        self.store.initialize()
        self.policy_ids = ["test-policy-" + str(uuid4()) for _ in parents]
        self.operator = "local-uid:" + str(os.geteuid())
        now = int(time.time())
        for parent, policy_id in zip(parents, self.policy_ids, strict=True):
            self.store.provision(
                {
                    "schema": "ananta.meet-dialog-preauthorization-policy.v1",
                    "policy_id": policy_id,
                    "tenant_id": principal.tenant_id,
                    "project_id": principal.project_id,
                    "parent_task_id": parent,
                    "owner_subject": principal.subject_id,
                    "origin": authority.binding.profile.origin,
                    "room_id": room,
                    "capabilities": sorted(authority.policies[(principal.tenant_id, principal.project_id)]),
                    "valid_from": now - 1,
                    "expires_at": now + 300,
                    "max_duration_seconds": duration_seconds,
                    "max_dispatches": 1,
                },
                0,
                self.operator,
            )
        authority.preauthorization = MeetDialogPreauthorization(self.store)

    def revoke(self, index):
        return self.store.revoke(self.policy_ids[index], 1, self.operator)

    def require_bound(self, task, index):
        binding = task.worker_execution_context["meet_preauthorization"]
        assert binding["policy_id"] == self.policy_ids[index] and binding["revision"] == 1
