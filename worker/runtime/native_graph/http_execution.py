"""Execution-only Native HTTP port; the consumer asks the Hub for current authority."""

import secrets

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ananta_contracts.native_result_maintenance import MAINTENANCE_TASK_KIND
from ananta_contracts.native_task_execution import MAX_NATIVE_TASK_HTTP_BYTES, verify_native_execution_envelope


def create_native_execution_blueprint(*, token_provider, recipient_provider, verification_provider=None, consumer=None,
                                      result_store_provider=None, evidence_consumer=None, result_store_resolver=None,
                                      execution_admission=None):
    if verification_provider is None:
        from worker.runtime.native_graph.authorization import load_ed25519_verification_key_ring

        verification_provider = load_ed25519_verification_key_ring
    if consumer is None:
        from worker.runtime.workflow_adapter_task_execution import (
            authorize_delegated_native_task,
            consume_delegated_workflow_task,
        )

        consumer = consume_delegated_workflow_task
        if execution_admission is None:
            execution_admission = authorize_delegated_native_task
    blueprint = Blueprint("native_task_execution", __name__)

    @blueprint.post("/api/internal/native-tasks/result")
    @blueprint.post("/api/internal/native-tasks/execute")
    def execute():
        if str(current_app.config.get("ROLE") or "").strip().lower() != "worker":
            return jsonify(reason_code="native_execution_worker_required"), 403
        try:
            token = token_provider()
        except Exception:
            token = None
        if not isinstance(token, str) or len(token) < 32:
            return jsonify(reason_code="native_execution_auth_unavailable"), 503
        if not secrets.compare_digest(request.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
            return jsonify(reason_code="native_execution_auth_required"), 401
        if not request.is_json:
            return jsonify(reason_code="native_execution_json_required"), 400
        if request.content_length is None or not 0 < request.content_length <= MAX_NATIVE_TASK_HTTP_BYTES:
            return jsonify(reason_code="native_execution_body_limit"), 413
        request.max_content_length = MAX_NATIVE_TASK_HTTP_BYTES
        try:
            worker_id, worker_url = recipient_provider()
            verified = verify_native_execution_envelope(
                request.get_json(silent=True), key_ring=verification_provider(),
                worker_id=worker_id, worker_url=worker_url,
            )
            task = verified.task
        except RequestEntityTooLarge:
            return jsonify(reason_code="native_execution_body_limit"), 413
        except ValueError:
            return jsonify(reason_code="native_execution_projection_denied"), 422
        except Exception:
            return jsonify(reason_code="native_execution_verification_unavailable"), 503
        if verified.evidence_assignment is not None and evidence_consumer is None:
            return jsonify(reason_code="native_execution_evidence_consumer_unavailable"), 503
        if (task['task_kind'] == MAINTENANCE_TASK_KIND and request.path.endswith('/execute')
                and execution_admission is not None):
            try:
                if execution_admission(task) is not True:
                    return jsonify(reason_code='native_execution_authority_denied'), 409
            except Exception:
                return jsonify(reason_code='native_execution_authority_unavailable'), 503
        store_binding = ({"evidence_assignment": verified.evidence_assignment}
                         if verified.evidence_assignment is not None else {})
        store = None
        if result_store_provider is not None or result_store_resolver is not None:
            try:
                store = result_store_resolver(task) if result_store_resolver is not None else result_store_provider()
            except Exception:
                return jsonify(reason_code="native_result_store_unavailable"), 503
        if task['task_kind'] == MAINTENANCE_TASK_KIND:
            store_binding = {**store_binding, 'expire_before': False}
        if request.path.endswith("/result"):
            if store is None:
                return jsonify(reason_code="native_result_store_unavailable"), 503
            try:
                return jsonify(store.read(task, **store_binding))
            except Exception:
                return jsonify(reason_code="native_result_not_available"), 409
        try:
            if store is not None:
                store.reserve(task, **store_binding)
            result = (evidence_consumer(task, verified.evidence_assignment)
                      if verified.evidence_assignment is not None else consumer(task))
        except Exception:
            return jsonify(reason_code="native_execution_consumer_failed"), 409
        if not isinstance(result, dict):
            return jsonify(reason_code="native_execution_context_unsupported"), 422
        if store is not None:
            try:
                store.publish(task, result, **store_binding)
            except Exception:
                return jsonify(reason_code="native_result_publication_failed"), 409
        return jsonify(result)

    return blueprint
