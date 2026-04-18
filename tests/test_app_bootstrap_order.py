from flask import Flask


def test_create_app_orchestrates_named_bootstrap_steps(monkeypatch):
    import agent.ai_agent as ai

    calls = []

    def record(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            if name == "build_base_app_config":
                return {"AGENT_NAME": args[0]}

        return _inner

    monkeypatch.setattr(ai, "setup_logging", record("setup_logging"))
    monkeypatch.setattr(ai, "setup_signal_handlers", record("setup_signal_handlers"))
    monkeypatch.setattr(ai, "log_runtime_hints", record("log_runtime_hints"))
    monkeypatch.setattr(ai, "configure_audit_logger", record("configure_audit_logger"))
    monkeypatch.setattr(ai, "init_db", record("init_db"))
    monkeypatch.setattr(ai, "register_request_hooks", record("register_request_hooks"))
    monkeypatch.setattr(ai, "register_error_handler", record("register_error_handler"))
    monkeypatch.setattr(ai, "configure_cors", record("configure_cors"))
    monkeypatch.setattr(ai, "build_base_app_config", record("build_base_app_config"))
    monkeypatch.setattr(ai, "configure_swagger", record("configure_swagger"))
    monkeypatch.setattr(ai, "register_blueprints", record("register_blueprints"))
    monkeypatch.setattr(ai, "register_alias_routes", record("register_alias_routes"))
    monkeypatch.setattr(ai, "initialize_runtime_state", record("initialize_runtime_state"))
    monkeypatch.setattr(ai, "load_extensions", record("load_extensions"))
    monkeypatch.setattr(ai, "initialize_repository_registry", record("initialize_repository_registry"))
    monkeypatch.setattr(ai, "initialize_core_services", record("initialize_core_services"))
    monkeypatch.setattr(ai, "start_background_services", record("start_background_services"))
    monkeypatch.setattr(ai.APP_STARTUP_DURATION, "set", record("record_startup_duration"))

    app = ai.create_app(agent="order-test")

    assert isinstance(app, Flask)
    assert app.config["AGENT_NAME"] == "order-test"
    assert calls == [
        "setup_logging",
        "setup_signal_handlers",
        "log_runtime_hints",
        "configure_audit_logger",
        "init_db",
        "register_request_hooks",
        "register_error_handler",
        "configure_cors",
        "build_base_app_config",
        "configure_swagger",
        "register_blueprints",
        "register_alias_routes",
        "initialize_runtime_state",
        "load_extensions",
        "initialize_repository_registry",
        "initialize_core_services",
        "start_background_services",
        "record_startup_duration",
    ]
