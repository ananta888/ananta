import logging
import threading
import time
import os
from agent.config import settings
from agent.database import OperationalError

def _is_db_operational_error(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) or "OperationalError" in str(exc)

def _sleep_with_shutdown(total_seconds: int) -> None:
    import agent.common.context
    for _ in range(total_seconds):
        if agent.common.context.shutdown_requested:
            break
        time.sleep(1)

def start_monitoring_thread(app):
    from agent.routes.system import check_all_agents_health, record_stats

    def should_run_monitoring() -> bool:
        return settings.role == "hub" or os.path.exists(app.config["AGENTS_PATH"])

    def log_monitoring_error(exc: Exception, db_error_count: int) -> int:
        if not _is_db_operational_error(exc):
            logging.error(f"Fehler im Monitoring-Task: {exc}")
            return db_error_count
        db_error_count += 1
        if db_error_count <= 3:
            logging.info(f"Datenbank vorübergehend nicht erreichbar (Monitoring): {exc}")
        elif db_error_count % 10 == 0:
            logging.warning(
                f"Datenbank weiterhin nicht erreichbar (Monitoring, {db_error_count} Versuche): {exc}"
            )
        return db_error_count

    def run_monitoring():
        import agent.common.context
        if not should_run_monitoring():
            logging.info("Monitoring-Task uebersprungen (kein Hub und keine Agents-Daten).")
            return
        logging.info("Monitoring-Task gestartet.")
        db_error_count = 0
        while not agent.common.context.shutdown_requested:
            try:
                check_all_agents_health()
                record_stats()
                db_error_count = 0
            except Exception as e:
                db_error_count = log_monitoring_error(e, db_error_count)
            _sleep_with_shutdown(300)
        logging.info("Monitoring-Task beendet.")

    t = threading.Thread(target=run_monitoring, daemon=True)
    import agent.common.context
    agent.common.context.active_threads.append(t)
    t.start()
