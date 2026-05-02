from __future__ import annotations

from flask import Flask, jsonify

from .backends.mock import MockVoiceBackend
from .config import VoiceRuntimeConfig
from .routes import voice_runtime_bp


def create_app(config: VoiceRuntimeConfig | None = None) -> Flask:
    app = Flask(__name__)
    runtime_config = config or VoiceRuntimeConfig.from_env()
    backend = _build_backend(runtime_config)

    app.config["voice_runtime_config"] = runtime_config
    app.config["voice_runtime_backend"] = backend
    app.register_blueprint(voice_runtime_bp)

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        return jsonify({"error": {"code": "voice.internal_error", "message": str(exc), "retriable": False}}), 500

    return app


def _build_backend(config: VoiceRuntimeConfig):
    if config.backend == "mock":
        return MockVoiceBackend(model=f"mock-{config.model}")
    raise RuntimeError(f"unsupported VOICE_RUNTIME_BACKEND: {config.backend}")


if __name__ == "__main__":
    cfg = VoiceRuntimeConfig.from_env()
    create_app(cfg).run(host=cfg.host, port=cfg.port)
