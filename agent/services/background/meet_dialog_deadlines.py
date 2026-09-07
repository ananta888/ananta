"""Hub lifecycle tick for terminal bookkeeping; never a Worker execution loop."""

import logging
import threading

EXTENSION = "meet_dialog_deadline_reconciler"
RUNNER = "meet_dialog_deadlines"


def start_meet_dialog_deadlines(app):
    if app.config.get("ROLE") != "hub" or getattr(app, "testing", False) or RUNNER not in app.extensions:
        return
    existing = app.extensions.get(EXTENSION)
    if existing and existing["thread"].is_alive():
        return
    stop = threading.Event()
    runner = app.extensions[RUNNER]

    def run():
        while not stop.is_set():
            try:
                with app.app_context():
                    counts = runner.run_once(limit=25, stopped=stop.is_set)
                    if counts["invalid"]:
                        logging.warning("Meet deadline reconciliation rejected %d malformed tasks", counts["invalid"])
            except Exception as error:
                logging.warning("Meet deadline reconciliation unavailable: %s", type(error).__name__)
            stop.wait(5)

    thread = threading.Thread(target=run, name="meet-dialog-deadlines", daemon=True)
    app.extensions[EXTENSION] = {"thread": thread, "stop_event": stop}
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_meet_dialog_deadlines(app):
    state = getattr(app, "extensions", {}).get(EXTENSION)
    if state:
        state["stop_event"].set()
