import logging
import threading
import time
from agent.config import settings
from agent.utils import register_with_hub

def start_registration_thread(app):
    def run_register():
        import agent.common.context

        register_as_worker = settings.role == "worker" or (settings.role == "hub" and settings.hub_can_be_worker)
        if not register_as_worker:
            return

        max_retries = 10
        base_delay = 2

        for i in range(max_retries):
            if agent.common.context.shutdown_requested:
                logging.info("Hub-Registrierung wegen Shutdown abgebrochen.")
                break

            silent = i < 3
            success = register_with_hub(
                hub_url=settings.hub_url,
                agent_name=(
                    app.config["AGENT_NAME"]
                    if settings.role == "worker"
                    else f"{app.config['AGENT_NAME']}-local-worker"
                ),
                port=settings.port,
                token=app.config["AGENT_TOKEN"],
                role="worker",
                silent=silent,
            )

            if success:
                return

            delay = min(base_delay * (2**i), 300)
            if not silent:
                logging.warning(f"Hub-Registrierung fehlgeschlagen. Retry {i + 1}/{max_retries} in {delay}s...")
            else:
                logging.info(f"Hub noch nicht bereit, erneuter Versuch in {delay}s... (Versuch {i + 1})")

            for _ in range(delay):
                if agent.common.context.shutdown_requested:
                    break
                time.sleep(1)

    t = threading.Thread(target=run_register, daemon=True)
    import agent.common.context
    agent.common.context.active_threads.append(t)
    t.start()
