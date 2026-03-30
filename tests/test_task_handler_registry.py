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
        handler = get_task_handler_registry(app).resolve("plugin_task")
        assert handler is not None
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
        register_task_handler("plugin_task", DemoTaskHandler(), app=app)
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

        execute_response = service.execute_task_step(
            "T-PLUGIN",
            TaskStepExecuteRequest(command="ignored"),
            forwarder=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("forwarder should not be called")),
        )
        assert execute_response.data["status"] == "completed"
        assert execute_response.data["output"] == "plugin-output"
