"""Opt-in lifecycle-owned Hub tick; policy and SQL claims remain in services."""

import logging
import os
import threading

EXTENSION = "persona_retention_reconciler"
VIDEO_EXTENSION = "persona_video_retention_reconciler"
VOICE_EXTENSION = "persona_voice_retention_reconciler"


def start_persona_retention(app):
    _start(app, extension=EXTENSION, flag="ANANTA_PERSONA_RETENTION_ENABLED", runner_key="persona_retention_runner")
    _start(
        app,
        extension=VIDEO_EXTENSION,
        flag="ANANTA_PERSONA_VIDEO_RETENTION_ENABLED",
        runner_key="persona_video_retention_runner",
    )
    _start(
        app,
        extension=VOICE_EXTENSION,
        flag="ANANTA_PERSONA_VOICE_RETENTION_ENABLED",
        runner_key="persona_voice_retention_runner",
    )


def _start(app, *, extension, flag, runner_key):
    if app.config.get("ROLE") != "hub" or os.environ.get(flag) != "1" or runner_key not in app.extensions:
        return
    existing = app.extensions.get(extension)
    if existing and existing["thread"].is_alive():
        return
    stop = threading.Event()
    runner = app.extensions[runner_key]

    def run():
        while not stop.is_set():
            try:
                with app.app_context():
                    runner.run_once(limit=5, stopped=stop.is_set)
            except Exception as error:
                # No exception text, path, asset content, bearer or principal.
                logging.warning("Persona retention tick unavailable: %s", type(error).__name__)
            stop.wait(60)

    name = extension.removesuffix("_reconciler").replace("_", "-")
    thread = threading.Thread(target=run, name=name, daemon=True)
    app.extensions[extension] = {"thread": thread, "stop_event": stop}
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_persona_retention(app):
    for extension in (EXTENSION, VIDEO_EXTENSION, VOICE_EXTENSION):
        state = getattr(app, "extensions", {}).get(extension)
        if state:
            state["stop_event"].set()
