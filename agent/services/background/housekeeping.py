import logging
import os
import threading
import time

from agent.config import settings
from agent.database import OperationalError
from agent.utils import (
    _archive_old_tasks,
    _archive_terminal_logs,
    _cleanup_old_backups,
    read_json,
)


def _is_db_operational_error(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) or "OperationalError" in str(exc)


def _sleep_with_shutdown(total_seconds: int) -> None:
    import agent.common.context

    for _ in range(total_seconds):
        if agent.common.context.shutdown_requested:
            break
        time.sleep(1)


def _check_token_rotation(app):
    """Prüft, ob der Token rotiert werden muss."""
    if str((app.config or {}).get("AGENT_TOKEN_FILE") or settings.agent_token_file or "").strip():
        return
    token_path = settings.token_path
    if not token_path or not os.path.exists(token_path):
        return

    try:
        token_data = read_json(token_path)
        last_rotation = token_data.get("last_rotation", 0)

        rotation_interval = settings.token_rotation_days * 86400
        if time.time() - last_rotation > rotation_interval:
            logging.info("Token-Rotations-Intervall erreicht. Starte Rotation...")
            with app.app_context():
                from agent.auth import rotate_token

                rotate_token()
    except (OperationalError, Exception) as e:
        is_db_err = _is_db_operational_error(e)
        if is_db_err:
            logging.info(f"Datenbank vorübergehend nicht erreichbar bei Token-Rotation: {e}")
        else:
            logging.error(f"Fehler bei der Prüfung der Token-Rotation: {e}")


def start_housekeeping_thread(app):
    def run_housekeeping():
        import agent.common.context

        logging.info("Housekeeping-Task gestartet.")
        consecutive_db_errors = 0
        while not agent.common.context.shutdown_requested:
            try:
                _archive_terminal_logs()
                _cleanup_old_backups()
                _archive_old_tasks(app.config["TASKS_PATH"])
                _check_token_rotation(app)
                if settings.role == "hub":
                    from agent.services.voice_retention_cleanup_service import (
                        get_voice_retention_cleanup_service,
                    )

                    retention_counts = get_voice_retention_cleanup_service().run_once()
                    logging.info("Voice retention cleanup completed: %s", retention_counts)
                    from agent.services.voice_idempotency_service import VoiceIdempotencyService

                    expired_idempotency = VoiceIdempotencyService().purge_expired()
                    logging.info(
                        "Voice idempotency retention cleanup completed: expired=%s",
                        expired_idempotency,
                    )
                    from agent.services.voice_live_run_maintenance_service import (
                        get_voice_live_run_maintenance_service,
                    )

                    live_run_counts = get_voice_live_run_maintenance_service().run_once()
                    logging.info(
                        "Voice live-run maintenance completed: %s",
                        live_run_counts,
                    )
                    from agent.services.voice_runtime_cleanup_service import (
                        get_voice_runtime_cleanup_service,
                    )

                    recovered_scopes = get_voice_runtime_cleanup_service().retry_all_pending()
                    logging.info("Voice runtime cleanup replay completed: scopes=%s", recovered_scopes)
                consecutive_db_errors = 0
            except (OperationalError, Exception) as e:
                is_db_err = _is_db_operational_error(e)
                if is_db_err:
                    consecutive_db_errors += 1
                    if consecutive_db_errors <= 2:
                        logging.info(f"Datenbank vorübergehend nicht erreichbar (Housekeeping): {e}")
                    else:
                        logging.warning(
                            "Datenbank weiterhin nicht erreichbar "
                            f"(Housekeeping, {consecutive_db_errors} Versuche): {e}"
                        )
                else:
                    logging.error(f"Fehler im Housekeeping-Task: {e}")
            _sleep_with_shutdown(600)
        logging.info("Housekeeping-Task beendet.")

    t = threading.Thread(target=run_housekeeping, daemon=True)
    import agent.common.context

    agent.common.context.active_threads.append(t)
    t.start()
