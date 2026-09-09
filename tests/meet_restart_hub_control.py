"""Fixed authenticated operations on a private synthetic Hub, never production routes."""

import hmac
import re

from flask import abort, jsonify, request


def register_control(app, service, principal, control_key):
    if not isinstance(control_key, bytes) or len(control_key) != 32:
        raise ValueError("test_hub_control_key_invalid")

    def authorized():
        if (
            request.args
            or request.content_length not in {None, 0}
            or request.headers.get("Transfer-Encoding")
            or not hmac.compare_digest(request.headers.get("X-Test-Hub-Control", ""), control_key.hex())
        ):
            abort(403)

    def task(identifier):
        if not re.fullmatch(r"[a-f0-9-]{36}", identifier):
            abort(404)
        row = service.tasks.get_by_id(identifier)
        if row is None or (row.tenant_id, row.project_id, row.task_kind) != (
            "synthetic",
            "synthetic",
            "meet_dialog_session",
        ):
            abort(404)
        return row

    @app.get("/__test/health")
    def health():
        return jsonify(ready=True)

    @app.post("/__test/start")
    def start():
        authorized()
        if service.tasks.list_page("synthetic", "synthetic", 0):
            abort(409)
        result = service.start(
            principal,
            "synthetic",
            {
                "capabilities": ["video.receive"],
                "duration_seconds": 120,
                "chat_mode": "off",
            },
            parent="meet-test-parent",
        )
        return jsonify(task_id=result["task_id"])

    @app.post("/__test/enable/<identifier>")
    def enable(identifier):
        authorized()
        row = task(identifier)
        controls = row.worker_execution_context["meet_dialog"]["controls"]
        service.control(
            principal,
            "synthetic",
            identifier,
            {
                "expected_revision": controls["revision"],
                "chat": False,
                "audio": False,
                "screen": False,
                "visual": True,
            },
        )
        return jsonify(enabled=True)

    @app.get("/__test/status/<identifier>")
    def status(identifier):
        authorized()
        row = task(identifier)
        job = row.worker_execution_context.get("meet_visual", {}).get("job")
        child = service.tasks.get_by_id(job["task_id"]) if job is not None else None
        return jsonify(
            status=row.status,
            deadline=row.worker_execution_context["meet_dialog"]["deadline"],
            child_status=child.status if child is not None else None,
            child_deadline=job["deadline"] if job is not None else None,
            deadline_events=sum(item.get("event_type") == "meet_dialog_deadline_expired" for item in row.history),
            dialog_tasks=len(service.tasks.list_page("synthetic", "synthetic", 0)),
        )
