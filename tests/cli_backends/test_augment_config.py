from agent.services.augment.augment_config import (
    AugmentConfig,
    AugmentCliConfig,
    AugmentMcpConfig,
    AugmentBridgeConfig,
    AugmentSecurityConfig,
    load_augment_config,
    validate_augment_config,
)


def test_default_config_disabled():
    cfg = AugmentConfig()
    assert cfg.enabled is False
    assert cfg.mode == "disabled"


def test_allow_write_without_task_scoped_copy_invalid():
    cfg = AugmentConfig()
    cfg.auggie_cli.allow_write = True
    cfg.security.workspace_mode = "direct"
    issues = validate_augment_config(cfg)
    assert any("allow_write" in i for i in issues)


def test_send_secrets_invalid():
    cfg = AugmentConfig()
    cfg.security.send_secrets = True
    issues = validate_augment_config(cfg)
    assert any("send_secrets" in i for i in issues)


def test_denied_paths_not_empty():
    cfg = AugmentConfig()
    assert len(cfg.security.denied_paths) > 0
    assert ".env" in cfg.security.denied_paths


def test_load_from_empty_dict():
    cfg = load_augment_config({})
    assert cfg.enabled is False


def test_interactive_bridge_without_approval_invalid():
    cfg = AugmentConfig()
    cfg.interactive_bridge.enabled = True
    cfg.interactive_bridge.approval_required_for_write = False
    issues = validate_augment_config(cfg)
    assert any("interactive_bridge" in i for i in issues)


def test_load_from_none():
    cfg = load_augment_config(None)
    assert cfg.enabled is False
    assert cfg.mode == "disabled"


def test_load_enabled_from_dict():
    cfg = load_augment_config({"enabled": True, "mode": "mcp"})
    assert cfg.enabled is True
    assert cfg.mode == "mcp"


def test_load_nested_mcp_config():
    cfg = load_augment_config({"mcp": {"enabled": True}})
    assert cfg.mcp.enabled is True


def test_load_nested_security_config():
    cfg = load_augment_config({"security": {"workspace_mode": "direct", "send_secrets": False}})
    assert cfg.security.workspace_mode == "direct"


def test_valid_config_no_issues():
    cfg = AugmentConfig()
    issues = validate_augment_config(cfg)
    assert issues == []


def test_allow_write_with_task_scoped_copy_is_valid():
    cfg = AugmentConfig()
    cfg.auggie_cli.allow_write = True
    # workspace_mode defaults to task_scoped_copy
    issues = validate_augment_config(cfg)
    assert not any("allow_write" in i for i in issues)


def test_mcp_default_tool_name():
    cfg = AugmentConfig()
    assert cfg.mcp.tool_name == "codebase-retrieval"


def test_cli_default_args_contain_print_and_quiet():
    cfg = AugmentConfig()
    assert "--print" in cfg.auggie_cli.default_args
    assert "--quiet" in cfg.auggie_cli.default_args


def test_enabled_without_explicit_project_approval_warns():
    cfg = AugmentConfig()
    cfg.enabled = True
    cfg.security.require_explicit_project_approval = False
    issues = validate_augment_config(cfg)
    assert any("explicit project approval" in i for i in issues)
