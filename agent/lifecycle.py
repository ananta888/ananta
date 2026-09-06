import logging
import os
import signal
import threading

from agent.config import settings

BACKGROUND_SERVICE_NAMES = (
    "registration",
    "llm_monitoring",
    "monitoring",
    "planning_learning",
    "housekeeping",
    "workflow_runtime_reconciler",
    "ml_intern_training_reconciler",
    "speech_adaptation_dispatcher",
    "speech_evidence_retention_reconciler",
    "agent_safety_retention_reconciler",
    "persona_retention_reconciler",
    "semantic_media_audit_reconciler",
    "mail_polling_scheduler",
    "sfu_broadcast_reconciler_scheduler",
    "speech_reconciliation_reconciler",
    "speech_reconciliation_queue_pump",
    "speech_reconciliation_result_collector",
    "scheduler",
)


class BackgroundServiceManager:
    """Verwaltet den Lebenszyklus von Hintergrund-Threads."""

    def __init__(self, app):
        self.app = app
        self.threads = []
        self.started_services: list[str] = []
        self.failed_services: dict[str, str] = {}
        self.shutdown_requested = False

    def start_all(self):
        """Startet alle konfigurierten Hintergrunddienste."""
        if self._is_testing():
            logging.info("Background threads disabled (testing mode).")
            return

        if self._should_skip_for_reloader():
            # Signal-Handler zurücksetzen, wenn der Reloader noch nicht im Hauptprozess ist
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            return

        self._start_service("registration", self._start_registration)
        if not settings.disable_llm_check:
            self._start_service("llm_monitoring", self._start_llm_monitoring)
        self._start_service("monitoring", self._start_monitoring)
        if self._planning_learning_enabled():
            self._start_service("planning_learning", self._start_planning_learning)
        self._start_service("housekeeping", self._start_housekeeping)
        self._start_service(
            "workflow_runtime_reconciler",
            self._start_workflow_runtime_reconciler,
        )
        self._start_service(
            "ml_intern_training_reconciler",
            self._start_ml_intern_training_reconciler,
        )
        self._start_service(
            "speech_adaptation_dispatcher",
            self._start_speech_adaptation_dispatcher,
        )
        self._start_service(
            "speech_evidence_retention_reconciler",
            self._start_speech_evidence_retention_reconciler,
        )
        self._start_service(
            "agent_safety_retention_reconciler",
            self._start_agent_safety_retention_reconciler,
        )
        self._start_service("persona_retention_reconciler", self._start_persona_retention)
        self._start_service(
            "semantic_media_audit_reconciler",
            self._start_semantic_media_audit_reconciler,
        )
        self._start_service(
            "mail_polling_scheduler",
            self._start_mail_polling_scheduler,
        )
        self._start_service(
            "sfu_broadcast_reconciler_scheduler",
            self._start_sfu_broadcast_reconciler_scheduler,
        )
        self._start_service(
            "speech_reconciliation_reconciler",
            self._start_speech_reconciliation_reconciler,
        )
        self._start_service(
            "speech_reconciliation_queue_pump",
            self._start_speech_reconciliation_queue_pump,
        )
        self._start_service(
            "speech_reconciliation_result_collector",
            self._start_speech_reconciliation_result_collector,
        )
        self._start_service("scheduler", self._start_scheduler)
        self._capture_active_threads()
        extensions = getattr(self.app, "extensions", None)
        if isinstance(extensions, dict):
            extensions["background_services"] = self.runtime_state()

    def shutdown(self, *, join_timeout: float = 1.0) -> dict:
        """Fordert Shutdown an und stoppt explizit kontrollierte Background-Services."""
        import agent.common.context

        if self.shutdown_requested:
            return self.runtime_state()

        self.shutdown_requested = True
        agent.common.context.shutdown_requested = True
        try:
            # Stop new training dispatch and fence every active lease before
            # other speech services are drained.
            self._stop_speech_adaptation_dispatcher()
        except Exception as exc:
            self.failed_services["speech_adaptation_dispatcher_stop"] = str(exc)
        try:
            self._stop_ml_intern_training_reconciler()
        except Exception as exc:
            self.failed_services["ml_intern_training_reconciler_stop"] = str(exc)
        for name, stopper in (
            # Stop new dispatch first so no attempt can be claimed between
            # the collector's final DB fence and queue-pump shutdown.
            ("speech_reconciliation_queue_pump", self._stop_speech_reconciliation_queue_pump),
            ("speech_reconciliation_result_collector", self._stop_speech_reconciliation_result_collector),
            ("speech_reconciliation_reconciler", self._stop_speech_reconciliation_reconciler),
            ("semantic_media_audit_reconciler", self._stop_semantic_media_audit_reconciler),
            ("mail_polling_scheduler", self._stop_mail_polling_scheduler),
            ("sfu_broadcast_reconciler_scheduler", self._stop_sfu_broadcast_reconciler_scheduler),
            ("speech_evidence_retention_reconciler", self._stop_speech_evidence_retention_reconciler),
            ("agent_safety_retention_reconciler", self._stop_agent_safety_retention_reconciler),
            ("persona_retention_reconciler", self._stop_persona_retention),
        ):
            try:
                stopper()
            except Exception as exc:
                self.failed_services[f"{name}_stop"] = str(exc)
        try:
            self._stop_scheduler()
        except Exception as exc:
            self.failed_services["scheduler_stop"] = str(exc)

        for thread in list(agent.common.context.active_threads):
            if thread is threading.current_thread():
                continue
            if thread.is_alive():
                thread.join(timeout=join_timeout)

        self.app.extensions["background_services"] = self.runtime_state()
        return self.app.extensions["background_services"]

    def runtime_state(self) -> dict:
        return {
            "started": list(self.started_services),
            "failed": dict(self.failed_services),
            "shutdown_requested": self.shutdown_requested,
            "active_thread_count": len(self.threads),
        }

    def _start_service(self, name: str, starter) -> None:
        try:
            starter()
            self.started_services.append(name)
        except Exception as exc:
            self.failed_services[name] = str(exc)
            logging.warning("Background service %s failed to start: %s", name, exc)

    def _capture_active_threads(self) -> None:
        import agent.common.context

        self.threads = list(agent.common.context.active_threads)

    def _is_testing(self) -> bool:
        return bool(
            self.app.testing
            or os.environ.get("PYTEST_CURRENT_TEST")
            or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").lower() in {"1", "true", "yes"}
        )

    def _should_skip_for_reloader(self) -> bool:
        return os.environ.get("WERKZEUG_RUN_MAIN") != "true" and os.environ.get("FLASK_DEBUG") == "1"

    def _start_registration(self):
        from agent.services.background.registration import start_registration_thread

        start_registration_thread(self.app)

    def _start_llm_monitoring(self):
        from agent.services.background.llm_check import start_llm_check_thread

        start_llm_check_thread(self.app)

    def _start_monitoring(self):
        from agent.services.background.monitoring import start_monitoring_thread

        start_monitoring_thread(self.app)

    def _start_housekeeping(self):
        from agent.services.background.housekeeping import start_housekeeping_thread

        start_housekeeping_thread(self.app)

    def _start_planning_learning(self):
        from agent.services.background.planning_learning import start_planning_learning_thread

        start_planning_learning_thread(self.app)

    def _start_workflow_runtime_reconciler(self):
        from agent.services.background.workflow_runtime_reconciler import (
            start_workflow_runtime_reconciler_thread,
        )

        start_workflow_runtime_reconciler_thread(self.app)

    def _start_ml_intern_training_reconciler(self):
        from agent.services.background.ml_intern_training_reconciler import (
            start_ml_intern_training_reconciler_thread,
        )

        start_ml_intern_training_reconciler_thread(self.app)

    def _start_speech_adaptation_dispatcher(self):
        from agent.services.background.speech_adaptation_dispatcher import (
            start_speech_adaptation_dispatcher_thread,
        )

        start_speech_adaptation_dispatcher_thread(self.app)

    def _start_speech_reconciliation_reconciler(self):
        from agent.services.background.speech_reconciliation_reconciler import (
            start_speech_reconciliation_reconciler_thread,
        )

        start_speech_reconciliation_reconciler_thread(self.app)

    def _start_speech_reconciliation_queue_pump(self):
        from agent.services.background.speech_reconciliation_queue_pump import (
            start_speech_reconciliation_queue_pump_thread,
        )

        start_speech_reconciliation_queue_pump_thread(self.app)

    def _start_speech_reconciliation_result_collector(self):
        from agent.services.background.speech_reconciliation_result_collector import (
            start_speech_reconciliation_result_collector_thread,
        )

        start_speech_reconciliation_result_collector_thread(self.app)

    def _start_speech_evidence_retention_reconciler(self):
        from agent.services.background.speech_evidence_retention_reconciler import (
            start_speech_evidence_retention_reconciler_thread,
        )

        start_speech_evidence_retention_reconciler_thread(self.app)

    def _start_persona_retention(self):
        from agent.services.background.persona_retention import start_persona_retention

        start_persona_retention(self.app)

    def _stop_persona_retention(self):
        from agent.services.background.persona_retention import stop_persona_retention

        stop_persona_retention(self.app)

    def _start_agent_safety_retention_reconciler(self):
        from agent.services.background.agent_safety_retention_reconciler import (
            start_agent_safety_retention_reconciler,
        )

        start_agent_safety_retention_reconciler(self.app)

    def _start_semantic_media_audit_reconciler(self):
        from agent.services.background.semantic_media_audit_reconciler import (
            start_semantic_media_audit_reconciler_thread,
        )

        start_semantic_media_audit_reconciler_thread(self.app)

    def _start_mail_polling_scheduler(self):
        from agent.services.background.mail_polling_scheduler import (
            start_mail_polling_scheduler,
        )

        start_mail_polling_scheduler(self.app)

    def _start_sfu_broadcast_reconciler_scheduler(self):
        from agent.services.background.sfu_broadcast_reconciler_scheduler import (
            start_sfu_broadcast_reconciler_scheduler,
        )

        start_sfu_broadcast_reconciler_scheduler(self.app)

    def _stop_speech_reconciliation_result_collector(self):
        from agent.services.background.speech_reconciliation_result_collector import (
            stop_speech_reconciliation_result_collector,
        )

        stop_speech_reconciliation_result_collector(self.app)

    def _stop_speech_reconciliation_queue_pump(self):
        from agent.services.background.speech_reconciliation_queue_pump import (
            stop_speech_reconciliation_queue_pump,
        )

        stop_speech_reconciliation_queue_pump(self.app)

    def _stop_speech_reconciliation_reconciler(self):
        from agent.services.background.speech_reconciliation_reconciler import (
            stop_speech_reconciliation_reconciler,
        )

        stop_speech_reconciliation_reconciler(self.app)

    def _stop_speech_evidence_retention_reconciler(self):
        from agent.services.background.speech_evidence_retention_reconciler import (
            stop_speech_evidence_retention_reconciler,
        )

        stop_speech_evidence_retention_reconciler(self.app)

    def _stop_agent_safety_retention_reconciler(self):
        from agent.services.background.agent_safety_retention_reconciler import (
            stop_agent_safety_retention_reconciler,
        )

        stop_agent_safety_retention_reconciler(self.app)

    def _stop_semantic_media_audit_reconciler(self):
        from agent.services.background.semantic_media_audit_reconciler import (
            stop_semantic_media_audit_reconciler,
        )

        stop_semantic_media_audit_reconciler(self.app)

    def _stop_mail_polling_scheduler(self):
        from agent.services.background.mail_polling_scheduler import (
            stop_mail_polling_scheduler,
        )

        stop_mail_polling_scheduler(self.app)

    def _stop_sfu_broadcast_reconciler_scheduler(self):
        from agent.services.background.sfu_broadcast_reconciler_scheduler import (
            stop_sfu_broadcast_reconciler_scheduler,
        )

        stop_sfu_broadcast_reconciler_scheduler(self.app)

    def _stop_ml_intern_training_reconciler(self):
        from agent.services.background.ml_intern_training_reconciler import (
            stop_ml_intern_training_reconciler,
        )

        stop_ml_intern_training_reconciler(self.app)

    def _stop_speech_adaptation_dispatcher(self):
        from agent.services.background.speech_adaptation_dispatcher import (
            stop_speech_adaptation_dispatcher,
        )

        stop_speech_adaptation_dispatcher(self.app)

    def _start_scheduler(self):
        from agent.services.scheduler_service import get_scheduler_service

        get_scheduler_service().start()

    def _stop_scheduler(self):
        from agent.services.scheduler_service import get_scheduler_service

        get_scheduler_service().stop()

    def _planning_learning_enabled(self) -> bool:
        app_config = getattr(self.app, "config", {}) or {}
        agent_cfg = app_config.get("AGENT_CONFIG") or {}
        planning_policy = agent_cfg.get("planning_policy") if isinstance(agent_cfg.get("planning_policy"), dict) else {}
        learning_loop = (
            planning_policy.get("learning_loop") if isinstance(planning_policy.get("learning_loop"), dict) else {}
        )
        return bool(learning_loop.get("enabled", False))
