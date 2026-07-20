from __future__ import annotations

import threading
from types import SimpleNamespace

from flask import Flask

from agent.config import settings
from agent.services.background import semantic_media_audit_reconciler as audit_module
from agent.services.background import speech_evidence_retention_reconciler as retention_module


def _hub(monkeypatch) -> Flask:
    monkeypatch.setattr(settings, "role", "hub")
    return Flask(__name__)


def test_speech_retention_loop_is_feature_gated_bounded_and_stoppable(monkeypatch) -> None:
    app = _hub(monkeypatch)
    called = threading.Event()

    class Reconciler:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_once(self, *, batch_size):
            assert batch_size == 7
            called.set()
            return SimpleNamespace(
                staged=1,
                completed=1,
                pending=0,
                skipped_active_references=0,
                failed=0,
            )

    monkeypatch.setattr(retention_module, "SpeechEvidenceRetentionReconciler", Reconciler)
    monkeypatch.setenv("ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("ANANTA_SEMANTIC_MEDIA_BACKGROUND_OPERATIONS_ENABLED", "true")
    monkeypatch.setenv("ANANTA_PEER_EVIDENCE_SYNC_ENABLED", "true")
    monkeypatch.setenv("ANANTA_SPEECH_EVIDENCE_RETENTION_BATCH_SIZE", "7")

    retention_module.start_speech_evidence_retention_reconciler_thread(app)
    assert called.wait(2)
    state = app.extensions["speech_evidence_retention_reconciler"]
    retention_module.stop_speech_evidence_retention_reconciler(app)
    state["thread"].join(timeout=2)
    assert not state["thread"].is_alive()


def test_audit_retention_loop_uses_composed_service_and_is_stoppable(monkeypatch) -> None:
    app = _hub(monkeypatch)
    called = threading.Event()
    leases_called = threading.Event()

    class AuditService:
        def delete_expired(self, *, limit):
            assert limit == 9
            called.set()
            return 1

    class LeaseRepository:
        def expire_due(self, *, limit):
            assert limit == 9
            leases_called.set()
            return 1

    app.extensions["semantic_media_audit_service"] = AuditService()
    app.extensions["semantic_lease_repository"] = LeaseRepository()
    monkeypatch.setenv("ANANTA_SEMANTIC_VISUAL_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("ANANTA_SEMANTIC_MEDIA_AUDIT_RETENTION_BATCH_SIZE", "9")

    audit_module.start_semantic_media_audit_reconciler_thread(app)
    assert called.wait(2)
    assert leases_called.wait(2)
    state = app.extensions["semantic_media_audit_reconciler"]
    audit_module.stop_semantic_media_audit_reconciler(app)
    state["thread"].join(timeout=2)
    assert not state["thread"].is_alive()


def test_background_loops_do_not_start_for_worker_role(monkeypatch) -> None:
    app = Flask(__name__)
    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setenv("ANANTA_SEMANTIC_VISUAL_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("ANANTA_SEMANTIC_MEDIA_BACKGROUND_OPERATIONS_ENABLED", "true")
    monkeypatch.setenv("ANANTA_PEER_EVIDENCE_SYNC_ENABLED", "true")
    retention_module.start_speech_evidence_retention_reconciler_thread(app)
    audit_module.start_semantic_media_audit_reconciler_thread(app)
    assert "speech_evidence_retention_reconciler" not in app.extensions
    assert "semantic_media_audit_reconciler" not in app.extensions
