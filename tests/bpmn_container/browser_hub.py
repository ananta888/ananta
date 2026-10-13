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
        from bpmn_container.browser_evidence import browser_evidence

        self.evidence = browser_evidence(hub.engine)

        @hub.app.get("/test/browser-bootstrap")
        def bootstrap():
            require_token("BPMN_BROWSER_TOKEN")
            if not self.active.is_set():
                return jsonify(ready=False), 202
            return jsonify(username="bpmn-owner", password=hub.public.password,
                           evidence_assignment=self.evidence.projection)

        @hub.app.post("/test/browser-report")
        def report():
            require_token("BPMN_BROWSER_TOKEN")
            body = request.get_json()
            if not isinstance(body, dict) or not isinstance(body.get("passed"), bool):
                return jsonify(error="browser_report_invalid"), 422
            if body.get('evidence_assignment') != self.evidence.projection:
                return jsonify(error='browser_evidence_assignment_mismatch'), 409
            self.report = body
            self.finished.set()
            return jsonify(accepted=True)

    def run(self):
        self.hub.public.active_workflow_id = "browser_authenticated_workflow"
        self.evidence.reserve()
        self.active.set()
        deadline = time.monotonic() + 90
        while not self.finished.is_set() and time.monotonic() < deadline:
            for workflow_id in ("browser_authenticated_workflow", "browser_control_workflow", "browser_retry_workflow"):
                binding = self.hub.public.facade.bindings.get(workflow_id)
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
        control_binding = self.hub.public.facade.bindings.get("browser_control_workflow")
        assert control_binding is not None, "browser_control_binding_missing"
        assert self.hub.observations(control_binding.run_id) == [], "cancelled_timer_delegated_successor"
        control_events = self.hub.events.list_events(tenant_id=control_binding.tenant_id,
                                                   run_id=control_binding.run_id)
        kinds = [event.event_type for event in control_events]
        for kind in ("paused", "resumed", "cancelled"):
            assert kinds.count("workflow.run." + kind) == 1, "browser_control_transition_missing_or_duplicated"
        cancelled_edges = [event.payload['edge_id'] for event in control_events
                           if event.event_type == 'workflow.bpmn.edge.cancelled']
        assert sorted(cancelled_edges) == ['Flow_1', 'Flow_2', 'Flow_3']
        retry_binding = self.hub.public.facade.bindings.get('browser_retry_workflow')
        assert retry_binding is not None, 'browser_retry_binding_missing'
        assert self.hub.observations(retry_binding.run_id) == [], 'unapproved_retry_delegated_worker'
        retry_events = self.hub.events.list_events(tenant_id=retry_binding.tenant_id, run_id=retry_binding.run_id)
        assert sum(event.event_type == 'workflow.run.retry_requested' for event in retry_events) == 1
        assert sum(event.event_type == 'workflow.run.cancelled' for event in retry_events) == 2
        assert sum(event.event_type == 'workflow.bpmn.edge.pending' for event in retry_events) == 3
        registry_observation = self.evidence.complete(self.report)
        return {**self.report, "tasks": observations, "cancelled_edges": cancelled_edges,
                "control_successor_tasks": 0, 'unapproved_retry_worker_tasks': 0,
                'hub_registry_browser_run': registry_observation}
