from unittest.mock import MagicMock, patch

from agent.services.ml_intern_adapter_service import MlInternAdapterService


def test_ml_intern_adapter_returns_disabled_when_not_enabled():
    service = MlInternAdapterService()
    result = service.invoke_spike(prompt="hello", agent_cfg={"ml_intern_spike": {"enabled": False}})
    assert result["ok"] is False
    assert result["error"] == "ml_intern_spike_disabled"


def test_ml_intern_adapter_invokes_bounded_command():
    service = MlInternAdapterService()
    cfg = {
        "ml_intern_spike": {
            "enabled": True,
            "command_template": "python -c print('ok')",
            "timeout_seconds": 120,
            "max_prompt_chars": 8000,
            "max_output_chars": 2000,
        }
    }
    with patch("agent.services.ml_intern_adapter_service.subprocess.run") as mock_run:
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = "ml-intern-ok"
        completed.stderr = ""
        mock_run.return_value = completed
        result = service.invoke_spike(prompt="run spike", agent_cfg=cfg)

    assert result["ok"] is True
    assert result["backend"] == "ml_intern"
    assert result["returncode"] == 0
    assert "working_dir" in result["bounded_execution"]
