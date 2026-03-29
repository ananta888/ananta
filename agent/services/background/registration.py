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
        _update_registration_state(max_retries=max_retries)

        for i in range(max_retries):
            if agent.common.context.shutdown_requested:
                logging.info("Hub-Registrierung wegen Shutdown abgebrochen.")
                _update_registration_state(running=False, last_error="shutdown_requested", next_retry_at=None)
                break

            silent = i < 3
            _update_registration_state(
                attempts=i + 1,
                last_attempt_at=time.time(),
                last_error=None,
                next_retry_at=None,
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
                _update_registration_state(running=False, last_success_at=time.time(), next_retry_at=None)
                return

            if i == max_retries - 1:
                _update_registration_state(running=False, last_error="registration_failed", next_retry_at=None)
                break

            delay = min(base_delay * (2**i), 300)
            _update_registration_state(last_error="registration_failed", next_retry_at=time.time() + delay)
            if not silent:
                logging.warning(f"Hub-Registrierung fehlgeschlagen. Retry {i + 1}/{max_retries} in {delay}s...")
            else:
                logging.info(f"Hub noch nicht bereit, erneuter Versuch in {delay}s... (Versuch {i + 1})")

            for _ in range(delay):
                if agent.common.context.shutdown_requested:
                    _update_registration_state(running=False, last_error="shutdown_requested", next_retry_at=None)
                    break
                time.sleep(1)

        _update_registration_state(running=False, next_retry_at=None)

    t = threading.Thread(target=run_register, daemon=True)
    import agent.common.context

    _update_registration_state(thread_started=True)
    agent.common.context.active_threads.append(t)
    t.start()
