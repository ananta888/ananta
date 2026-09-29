"""Fixed authenticated operations on a private synthetic Hub, never production routes."""

import hmac
import re

from flask import Blueprint, abort, jsonify, request


def register_control(app, service, principal, control_key, *, task_count):
    if not isinstance(control_key, bytes) or len(control_key) != 32:
        raise ValueError("test_hub_control_key_invalid")
    if not callable(task_count):
        raise ValueError("test_hub_task_counter_required")
    routes = Blueprint("meet_restart_test", __name__, url_prefix="/__test")

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

    @routes.get("/health")
    def health():
        return jsonify(ready=True)

    @routes.post("/start")
    def start():
        authorized()
        if task_count():
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

    @routes.post("/enable/<identifier>")
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

    @routes.get("/status/<identifier>")
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
            dialog_tasks=task_count(),
        )

    app.register_blueprint(routes)
