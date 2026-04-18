from flask import Flask


def start_background_services(app: Flask) -> None:
    from agent.lifecycle import BackgroundServiceManager

    BackgroundServiceManager(app).start_all()

