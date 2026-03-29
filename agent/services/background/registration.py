import logging
import threading
import time

from agent.config import settings
from agent.utils import register_with_hub

_registration_state_lock = threading.Lock()
_registration_state = {
    "enabled": False,
    "thread_started": False,
    "running": False,
    "attempts": 0,
    "max_retries": 0,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": None,
    "next_retry_at": None,
    "registered_as": None,
}


def _update_registration_state(**changes):
    with _registration_state_lock:
        _registration_state.update(changes)


def get_registration_state() -> dict:
    with _registration_state_lock:
        return dict(_registration_state)


def reset_registration_state() -> None:
    _update_registration_state(
        enabled=False,
        thread_started=False,
        running=False,
        attempts=0,
        max_retries=0,
        last_attempt_at=None,
        last_success_at=None,
        last_error=None,
        next_retry_at=None,
        registered_as=None,
    )


def _registration_refresh_interval() -> int:
    offline_timeout = int(getattr(settings, "agent_offline_timeout", 300) or 300)
    return max(30, min(offline_timeout // 2 if offline_timeout > 0 else 60, 300))


def _sleep_with_shutdown(total_seconds: int) -> bool:
    import agent.common.context

    for _ in range(max(0, int(total_seconds))):
        if agent.common.context.shutdown_requested:
            return False
        time.sleep(1)
    return not agent.common.context.shutdown_requested


def start_registration_thread(app):
    def run_register():
        import agent.common.context

        register_as_worker = settings.role == "worker" or (settings.role == "hub" and settings.hub_can_be_worker)
        registered_as = (
            app.config["AGENT_NAME"] if settings.role == "worker" else f"{app.config['AGENT_NAME']}-local-worker"
        )
        _update_registration_state(
            enabled=bool(register_as_worker),
            running=bool(register_as_worker),
            registered_as=registered_as if register_as_worker else None,
        )
        if not register_as_worker:
            _update_registration_state(running=False)
            return

        max_retries = 10
        base_delay = 2
        refresh_interval = _registration_refresh_interval()
        consecutive_failures = 0
        total_attempts = 0
        _update_registration_state(max_retries=max_retries)

        while not agent.common.context.shutdown_requested:
            total_attempts += 1
            silent = consecutive_failures < 3
            _update_registration_state(
                attempts=total_attempts,
                last_attempt_at=time.time(),
                last_error=None,
                next_retry_at=None,
                running=True,
            )
            success = register_with_hub(
                hub_url=settings.hub_url,
                agent_name=registered_as,
                port=settings.port,
                token=app.config["AGENT_TOKEN"],
                role="worker",
                silent=silent,
            )

            if success:
                consecutive_failures = 0
                _update_registration_state(last_success_at=time.time(), next_retry_at=time.time() + refresh_interval, last_error=None)
                if not _sleep_with_shutdown(refresh_interval):
                    break
                continue

            consecutive_failures += 1
            if consecutive_failures >= max_retries:
                _update_registration_state(last_error="registration_failed", next_retry_at=None)
                break

            delay = min(base_delay * (2 ** (consecutive_failures - 1)), 300)
            _update_registration_state(last_error="registration_failed", next_retry_at=time.time() + delay)
            if not silent:
                logging.warning(
                    f"Hub-Registrierung fehlgeschlagen. Retry {consecutive_failures}/{max_retries} in {delay}s..."
                )
            else:
                logging.info(f"Hub noch nicht bereit, erneuter Versuch in {delay}s... (Versuch {consecutive_failures})")

            if not _sleep_with_shutdown(delay):
                break

        if agent.common.context.shutdown_requested:
            _update_registration_state(running=False, last_error="shutdown_requested", next_retry_at=None)
            logging.info("Hub-Registrierung wegen Shutdown abgebrochen.")
            return
        _update_registration_state(running=False, next_retry_at=None)

    t = threading.Thread(target=run_register, daemon=True)
    import agent.common.context

    _update_registration_state(thread_started=True)
    agent.common.context.active_threads.append(t)
    t.start()
