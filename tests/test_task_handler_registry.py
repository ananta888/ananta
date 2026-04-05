from pathlib import Path

from flask import Flask

from agent.models import TaskStepExecuteRequest, TaskStepProposeRequest
from agent.plugin_loader import load_plugins
from agent.services.task_handler_registry import get_task_handler_registry, register_task_handler
from agent.services.task_scoped_execution_service import get_task_scoped_execution_service


def test_plugin_loader_registers_task_handler_plugin(tmp_path: Path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "demo_task_plugin.py"
    mod.write_text(
        "\n".join(
            [
                "from agent.services.task_handler_registry import register_task_handler",
                "",
                "class DemoTaskHandler:",
                "    def propose(self, **kwargs):",
                "        return {'status': 'plugin_proposed', 'task_id': kwargs['tid']}",
                "",
                "def init_app(app):",
                "    register_task_handler('plugin_task', DemoTaskHandler(), app=app)",
            ]
        ),
        encoding="utf-8",
    )

    from agent.config import settings

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert "demo_task_plugin" in loaded
        registry = get_task_handler_registry(app)
        handler = registry.resolve("plugin_task")
        assert handler is not None
        assert registry.resolve_descriptor("plugin_task") is not None
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins


def test_task_scoped_execution_service_uses_registered_handler(monkeypatch):
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {}
    app.config["AGENT_NAME"] = "test-agent"

    service = get_task_scoped_execution_service()

    class DemoTaskHandler:
        def propose(self, **kwargs):
            return {"status": "plugin_proposed", "task_id": kwargs["tid"], "reason": "plugin"}

        def execute(self, **kwargs):
            return {"status": "completed", "task_id": kwargs["tid"], "output": "plugin-output", "exit_code": 0}

    with app.app_context():
        register_task_handler(
            "plugin_task",
            DemoTaskHandler(),
            app=app,
            capabilities=["plugin_exec"],
            safety_flags={"requires_review": True},
            verification_hooks=["plugin_smoke"],
        )
        monkeypatch.setattr(
            service,
            "_require_task",
            lambda tid: {"id": tid, "task_kind": "plugin_task", "description": "Handled by plugin"},
        )

        propose_response = service.propose_task_step(
            "T-PLUGIN",
            TaskStepProposeRequest(prompt="Use plugin"),
            cli_runner=lambda **kwargs: (_ for _ in ()).throw(AssertionError("cli_runner should not be called")),
            forwarder=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("forwarder should not be called")),
            tool_definitions_resolver=lambda **kwargs: [],
        )
        assert propose_response.data["status"] == "plugin_proposed"
        assert propose_response.data["task_id"] == "T-PLUGIN"
        assert (propose_response.data.get("handler_contract") or {}).get("capabilities") == ["plugin_exec"]
        assert (propose_response.data.get("review") or {}).get("required") is True

        execute_response = service.execute_task_step(
            "T-PLUGIN",
            TaskStepExecuteRequest(command="ignored"),
            forwarder=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("forwarder should not be called")),
        )
        assert execute_response.data["status"] == "completed"
        assert execute_response.data["output"] == "plugin-output"
        assert (execute_response.data.get("handler_contract") or {}).get("verification_hooks") == ["plugin_smoke"]


def test_task_scoped_repair_proposal_preserves_cli_session():
    app = Flask(__name__)
    service = get_task_scoped_execution_service()
    captured: dict = {}
    session_payload = {
        "id": "sess-1",
        "metadata": {"opencode_runtime": {"kind": "native_server", "native_session_id": "native-1"}},
    }

    def _fake_cli_runner(**kwargs):
        captured.update(kwargs)
        return 0, '{"reason":"repair","command":"echo ok"}', "", str(kwargs.get("backend") or "")

    with app.app_context():
        repaired = service._repair_task_proposal(
            cli_runner=_fake_cli_runner,
            prompt="Original prompt",
            bad_output="{}",
            validation_error="missing_required_fields: command_or_tool_calls",
            timeout=60,
            task_kind="coding",
            policy_version="v1",
            cfg={"default_model": "model-a", "task_propose_repair_backend": "opencode"},
            primary_backend="opencode",
            primary_model="model-a",
            session=session_payload,
        )

    assert repaired is not None
    assert captured.get("session") == session_payload
