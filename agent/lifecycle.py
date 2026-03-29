import logging
import os
import signal
import threading
import time

from agent.config import settings


class BackgroundServiceManager:
    """Verwaltet den Lebenszyklus von Hintergrund-Threads."""

    def __init__(self, app):
        self.app = app
        self.threads = []

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

        self._start_registration()
        if not settings.disable_llm_check:
            self._start_llm_monitoring()
        self._start_monitoring()
        self._start_housekeeping()
        self._start_scheduler()

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
