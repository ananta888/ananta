"""Bounded browser coordination owned by the isolated Hub fixture."""

from __future__ import annotations

import os
import threading
import time

from bpmn_container.transport import require_token
from flask import jsonify, request


class BrowserGate:
    def __init__(self, hub):
        self.hub = hub
        self.finished = threading.Event()
        self.active = threading.Event()
        self.report = None
        self.enabled = os.getenv("BPMN_BROWSER_ENABLED") == "1"

        @hub.app.get("/test/ready")
        def ready():
            return jsonify(ready=True)

        if not self.enabled:
            return

        @hub.app.get("/test/browser-bootstrap")
        def bootstrap():
            require_token("BPMN_BROWSER_TOKEN")
            if not self.active.is_set():
                return jsonify(ready=False), 202
            return jsonify(username="bpmn-owner", password=hub.public.password)

        @hub.app.post("/test/browser-report")
        def report():
            require_token("BPMN_BROWSER_TOKEN")
            body = request.get_json()
            if not isinstance(body, dict) or not isinstance(body.get("passed"), bool):
                return jsonify(error="browser_report_invalid"), 422
            self.report = body
            self.finished.set()
            return jsonify(accepted=True)

    def run(self):
        self.hub.public.active_workflow_id = "browser_authenticated_workflow"
        self.active.set()
        deadline = time.monotonic() + 90
        while not self.finished.is_set() and time.monotonic() < deadline:
            binding = self.hub.public.facade.bindings.get("browser_authenticated_workflow")
            if binding is not None:
                self.hub.dispatch_ready(binding.run_id)
            self.hub.collect()
            self.hub.public.facade.reconcile_active()
            self.finished.wait(timeout=0.05)
        assert self.finished.is_set(), "browser_report_timeout"
        assert self.report["passed"], self.report
        workflow_id = self.report["workflow_id"]
        binding = self.hub.public.facade.bindings.get(workflow_id)
        observations = self.hub.observations(binding.run_id)
        assert sorted(row["node"] for row in observations) == ["Task_1", "Task_2"]
        assert all(row["status"] == "completed" for row in observations)
        return {**self.report, "tasks": observations}
