import logging
import os
import signal
import threading

from agent.config import settings

BACKGROUND_SERVICE_NAMES = ("registration", "llm_monitoring", "monitoring", "housekeeping", "scheduler")


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
        self._start_service("housekeeping", self._start_housekeeping)
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

    def _start_scheduler(self):
        from agent.services.scheduler_service import get_scheduler_service

        get_scheduler_service().start()

    def _stop_scheduler(self):
        from agent.services.scheduler_service import get_scheduler_service

        get_scheduler_service().stop()
