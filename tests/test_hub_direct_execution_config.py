"""HDE-002: configuration defaults and gates for hub-direct execution."""
from agent.config_defaults import build_default_agent_config
from agent.services.hub_direct_execution_router import HubDirectExecutionRouter


def test_default_config_block_exists_and_is_off():
    cfg = build_default_agent_config()
    block = cfg.get("hub_direct_execution")
    assert isinstance(block, dict)
    assert block["enabled"] is False


def test_default_config_has_all_required_fields():
    block = build_default_agent_config()["hub_direct_execution"]
    for field in (
        "allowed_tools",
        "max_result_chars",
        "confidence_threshold",
        "direct_before_worker",
        "audit_enabled",
        "require_policy_gate",
        "fallback_to_worker",
    ):
        assert field in block, field
    assert isinstance(block["allowed_tools"], list) and block["allowed_tools"]


def test_default_allowed_tools_are_read_only_safe():
    block = build_default_agent_config()["hub_direct_execution"]
    assert "repo.write_file" not in block["allowed_tools"]
    assert "repo.apply_patch" not in block["allowed_tools"]
    assert "shell.run_unrestricted" not in block["allowed_tools"]


def test_disabled_default_blocks_routing():
    cfg = build_default_agent_config()
    decision = HubDirectExecutionRouter().classify("git status", agent_cfg=cfg)
    assert decision.eligible is False
    assert decision.reason_code == "hub_direct_disabled"


def test_explicit_on_enables_routing():
    cfg = build_default_agent_config()
    cfg["hub_direct_execution"]["enabled"] = True
    decision = HubDirectExecutionRouter().classify("git status", agent_cfg=cfg)
    assert decision.eligible is True


def test_direct_before_worker_false_skips_proposal_path(monkeypatch):
    """direct_before_worker=false: propose_direct_step must not route."""
    from agent.services.task_execution_service import TaskExecutionService

    cfg = build_default_agent_config()
    cfg["hub_direct_execution"]["enabled"] = True
    cfg["hub_direct_execution"]["direct_before_worker"] = False

    class _Req:
        task_id = None
        prompt = "git status"

    result = TaskExecutionService()._try_hub_direct_execution(_Req(), prompt="git status", agent_cfg=cfg)
    assert result is None


def test_fallback_to_worker_behavior():
    """Not eligible + fallback on -> None (worker takes over)."""
    from agent.services.task_execution_service import TaskExecutionService

    cfg = build_default_agent_config()
    cfg["hub_direct_execution"]["enabled"] = True

    class _Req:
        task_id = None
        prompt = "implementiere ein feature"
        task_kind = None

    result = TaskExecutionService()._try_hub_direct_execution(_Req(), prompt=_Req.prompt, agent_cfg=cfg)
    assert result is None
