"""Optional real packaged browser execution with explicitly synthetic public HTML."""

import time

from agent.services.meet_browser_policy import MeetBrowserPolicy
from agent.services.meet_browser_tasks import HubBrowserTasks
from agent.services.meet_browser_workspaces import MeetBrowserWorkspaces


class MultiWorkerBrowserScenario:
    def __init__(self, enabled):
        self.enabled = enabled
        self.start_options = {"browser_workspace": True} if enabled else {}
        self.children = []

    def service_options(self, authority, tasks):
        if not self.enabled:
            return {}
        self.tasks = tasks
        policy = MeetBrowserPolicy(
            [
                {
                    "tenant_id": "synthetic",
                    "project_id": "synthetic",
                    "owner_subject": "owner",
                    "policy_id": "synthetic-packaged-browser",
                    "revision": 1,
                    "allowed_origins": ["https://example.com"],
                    "operations": ["navigate", "present"],
                }
            ]
        )
        self.browser = MeetBrowserWorkspaces(authority, HubBrowserTasks(tasks), policy)
        return {"browser_workspaces": self.browser}

    def exercise(self, app, principal, started, containers, command, record_property):
        if not self.enabled:
            return
        workspaces = []
        for parent in started:
            with app.app_context():
                result = self.browser.change(
                    principal,
                    "synthetic",
                    parent["task_id"],
                    {
                        "action": "navigate",
                        "expected_revision": 1,
                        "url": "https://example.com/docs",
                    },
                )
                assert result["mode"] == "off" and result["task_status"] == "in_progress"
                self.children.append(result["task_id"])
                scope = self.browser.owner_scope(principal, "synthetic", parent["task_id"])
                workspaces.append(self.browser.tasks.read(scope)["job"]["workspace_id"])
        assert len(set(self.children)) == len(set(workspaces)) == 2
        self.wait(containers, revision=2, mode="off")
        for parent in started:
            with app.app_context():
                self.browser.change(
                    principal,
                    "synthetic",
                    parent["task_id"],
                    {
                        "action": "present",
                        "expected_revision": 2,
                    },
                )
        self.wait(containers, revision=3, mode="browser")
        assert command("screens") == {"moving": [True, True], "departedAbsent": False}
        record_property(
            "two_packaged_browser_workspaces",
            {
                "synthetic_document_transport": True,
                "synthetic_policy": True,
                "installed_worker_source": True,
                "distinct_tasks_and_workspaces": True,
                "navigation_does_not_present": True,
                "decoded_meet_receivers": 2,
                "production_release_evidence": False,
            },
        )

    @staticmethod
    def wait(containers, *, revision, mode):
        deadline = time.monotonic() + 8
        states = []
        while time.monotonic() < deadline:
            states = [container.browser_state() for container in containers]
            if all(value == {"revision": revision, "mode": mode, "loaded": True, "failed": False} for value in states):
                return
            time.sleep(0.1)
        raise AssertionError({"browser_readiness_missing": states, "expected_revision": revision})

    def require_terminal(self, tasks):
        if self.enabled:
            assert len(self.children) == 3
            assert all(tasks.get_by_id(child).status in {"cancelled", "failed"} for child in self.children)

    def after_departure(self, app, principal, survivor, container, record_property):
        if not self.enabled:
            return
        # A room-membership epoch change retires the old browser assignment.
        # The surviving dialog does not gain permission to reload it implicitly.
        # This fixture's Hub creates a new separately admitted navigation task.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with app.app_context():
                old = self.tasks.get_by_id(self.children[1])
            if old.status in {"failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert old.status in {"failed", "cancelled"}, "stale membership-bound browser child was not retired"
        with app.app_context():
            task = survivor["task_id"]
            previous = self.browser.inspect(principal, "synthetic", task)
            result = self.browser.change(
                principal,
                "synthetic",
                task,
                {
                    "action": "navigate",
                    "expected_revision": previous["revision"],
                    "url": "https://example.com/docs",
                },
            )
            assert result["task_id"] not in self.children and result["mode"] == "off"
            self.children.append(result["task_id"])
            selected = self.browser.change(
                principal,
                "synthetic",
                task,
                {
                    "action": "present",
                    "expected_revision": result["revision"],
                },
            )
        self.wait([container], revision=selected["revision"], mode="browser")
        record_property(
            "packaged_browser_membership_replacement",
            {
                "old_task_terminal": True,
                "fresh_hub_navigation_required": True,
                "implicit_worker_replay": False,
                "production_release_evidence": False,
            },
        )
