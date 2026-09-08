"""Real browser/Hub/Meet composition with explicitly synthetic document transport."""

import json
import threading
import time
from functools import partial

from agent.services.meet_browser_policy import MeetBrowserPolicy
from agent.services.meet_browser_tasks import HubBrowserTasks
from agent.services.meet_browser_workspaces import MeetBrowserWorkspaces
from worker.meet_media.browser_public_workspace import PublicDocumentWorkspace
from worker.meet_media.dialog_browser_screen import DialogBrowserScreen


class BrowserWorkspaceScenario:
    start_options = {"browser_workspace": True, "duration_seconds": 120}

    def __init__(self, monkeypatch):
        self.loaded = threading.Event()
        self.inject_private = threading.Event()
        self.loads = []
        self.fetches = 0
        scenario = self

        class SyntheticDocumentTransport:
            def __init__(self, *, require_current):
                self.require_current = require_current

            def fetch(self, request, *, deadline):
                self.require_current()
                assert request["url"] == "https://example.com/docs"
                assert time.monotonic() < deadline
                scenario.fetches += 1
                return """<!doctype html><html><body style="background:#ff00ff">
                  <h1>Synthetic public task document</h1><p>Delegated safe view.</p>
                  <script>document.body.replaceChildren(document.createElement('input'))</script>
                  </body></html>"""

        class ObservedWorkspace(PublicDocumentWorkspace):
            def load(self, content, generation):
                super().load(content, generation)
                scenario.loads.append(self)
                scenario.loaded.set()

            def take(self, generation):
                if scenario.inject_private.is_set():
                    scenario.inject_private.clear()
                    # Only the source-owning Playwright thread may mutate this page.
                    self.page.evaluate("""() => {
                      document.body.replaceChildren(document.createElement('input'));
                      document.body.style.background = '#ff00ff';
                      document.querySelector('input').value = 'SYNTHETIC_PRIVATE_MARKER';
                    }""")
                return super().take(generation)

        monkeypatch.setattr(
            "worker.meet_media.dialog_runtime.DialogBrowserScreen",
            partial(DialogBrowserScreen, fetch_factory=SyntheticDocumentTransport, workspace_factory=ObservedWorkspace),
        )

    def configure(self, authority, tasks):
        policy = MeetBrowserPolicy(
            [
                {
                    "tenant_id": "synthetic",
                    "project_id": "synthetic",
                    "owner_subject": "owner",
                    "policy_id": "synthetic-public-browser",
                    "revision": 1,
                    "allowed_origins": ["https://example.com"],
                    "operations": ["navigate", "present"],
                }
            ]
        )
        self.coordinator = MeetBrowserWorkspaces(authority, HubBrowserTasks(tasks), policy)
        return self.coordinator

    def finish(self, app, service, principal, started, speech, command, completed, failures, record_property):
        parent = started["task_id"]

        def change(action):
            with app.app_context():
                state = self.coordinator.inspect(principal, "synthetic", parent)
                body = {"action": action, "expected_revision": state["revision"]}
                if action == "navigate":
                    body["url"] = "https://example.com/docs"
                return self.coordinator.change(principal, "synthetic", parent, body)

        def screen(enabled):
            with app.app_context():
                controls = service.inspect(principal, "synthetic", parent)["controls"]
                body = {name: value["enabled"] for name, value in controls.items() if name != "revision"}
                return service.control(
                    principal,
                    "synthetic",
                    parent,
                    body | {"screen": enabled, "expected_revision": controls["revision"]},
                )

        first = change("navigate")
        assert first["mode"] == "off" and first["task_status"] == "in_progress"
        assert command("screen_absent") == {"screen_absent": True}
        assert self.loaded.wait(8), failures
        assert self.fetches == len(self.loads) == 1
        assert change("present")["mode"] == "browser"
        observed = command("screen")
        assert observed == {"moving_screen": True}, json.dumps(
            {
                "receiver": observed,
                "failures": failures,
                "callbacks": speech.callbacks.report(),
                "transport": speech.transport.report(),
            }
        )
        screen(False)
        assert command("screen_absent") == {"screen_absent": True}
        screen(True)
        assert command("screen") == {"moving_screen": True}, failures
        assert self.fetches == len(self.loads) == 1, "presentation pause must retain the assigned workspace"
        self.inject_private.set()
        invalidated_at = time.monotonic()
        assert command("private_frame_absent") == {"private_frame_absent": True}, failures
        privacy_stop_ms = (time.monotonic() - invalidated_at) * 1000
        assert privacy_stop_ms <= 5000
        # A rejected source does not turn into a neutral fallback or kill chat.
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}, failures
        with app.app_context():
            assert service.tasks.get_by_id(first["task_id"]).status == "failed"
        assert self.loads[0].closed
        self.loaded.clear()
        second = change("navigate")
        assert second["task_id"] != first["task_id"]
        assert self.loaded.wait(8), failures
        change("present")
        assert command("screen") == {"moving_screen": True}, failures
        # Replace only this private fixture's Hub policy, never real operator config.
        self.coordinator.policy = MeetBrowserPolicy([])
        revoked_at = time.monotonic()
        assert command("screen_absent") == {"screen_absent": True}, failures
        policy_stop_ms = (time.monotonic() - revoked_at) * 1000
        assert policy_stop_ms <= 5000
        with app.app_context():
            assert service.tasks.get_by_id(second["task_id"]).status == "cancelled"
        assert change("status")["mode"] == "status"
        assert command("screen") == {"moving_screen": True}, failures
        with app.app_context():
            assert service.inspect(principal, "synthetic", parent, stop=True)["status"] == "cancelled"
        assert completed.wait(10), failures
        assert command("alone") == {"alone": True}
        assert self.fetches == len(self.loads) == 2 and all(workspace.closed for workspace in self.loads)
        record_property(
            "dialog_browser_workspace",
            {
                "synthetic_document_transport": True,
                "synthetic_policy": True,
                "actual_decoded_meet_receiver": True,
                "source_workspaces": len(self.loads),
                "pause_preserves_workspace": True,
                "privacy_stop_ms": round(privacy_stop_ms, 2),
                "policy_stop_ms": round(policy_stop_ms, 2),
                "production_release_evidence": False,
            },
        )
        return True
