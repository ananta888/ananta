"""Opt-in lifecycle-owned Hub tick; policy and SQL claims remain in services."""

import logging
import os
import threading

EXTENSION = "persona_retention_reconciler"


def start_persona_retention(app):
    if (
        app.config.get("ROLE") != "hub"
        or os.environ.get("ANANTA_PERSONA_RETENTION_ENABLED") != "1"
        or "persona_retention_runner" not in app.extensions
    ):
        return
    existing = app.extensions.get(EXTENSION)
    if existing and existing["thread"].is_alive():
        return
    stop = threading.Event()
    runner = app.extensions["persona_retention_runner"]

    def run():
        while not stop.is_set():
            try:
                with app.app_context():
                    runner.run_once(limit=5, stopped=stop.is_set)
            except Exception as error:
                # No exception text, path, asset content, bearer or principal.
                logging.warning("Persona retention tick unavailable: %s", type(error).__name__)
            stop.wait(60)

    thread = threading.Thread(target=run, name="persona-retention", daemon=True)
    app.extensions[EXTENSION] = {"thread": thread, "stop_event": stop}
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_persona_retention(app):
    state = getattr(app, "extensions", {}).get(EXTENSION)
    if state:
        state["stop_event"].set()
