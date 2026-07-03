from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AugmentMcpConfig:
    enabled: bool = False
    server_command: str = "auggie"
    server_args: list[str] = field(default_factory=lambda: ["mcp", "serve"])
    tool_name: str = "codebase-retrieval"
    timeout_seconds: int = 45
    max_results: int = 12

@dataclass
class AugmentCliConfig:
    enabled: bool = False
    command: str = "auggie"
    default_args: list[str] = field(default_factory=lambda: ["--print", "--quiet"])
    requires_login: bool = True
    timeout_seconds: int = 300
    max_output_bytes: int = 1048576
    allow_write: bool = False

@dataclass
class AugmentBridgeConfig:
    enabled: bool = False
    max_session_seconds: int = 1800
    idle_timeout_seconds: int = 120
    approval_required_for_write: bool = True

@dataclass
class AugmentSecurityConfig:
    default_network: str = "restricted"
    workspace_mode: str = "task_scoped_copy"
    send_secrets: bool = False
    redact_env: bool = True
    allowed_paths: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=lambda: [
        ".git", ".env", ".venv", "node_modules", "dist", "build",
        ".claude", ".augment", "secrets"
    ])
    require_explicit_project_approval: bool = True

@dataclass
class AugmentConfig:
    enabled: bool = False
    mode: str = "disabled"
    mcp: AugmentMcpConfig = field(default_factory=AugmentMcpConfig)
    auggie_cli: AugmentCliConfig = field(default_factory=AugmentCliConfig)
    interactive_bridge: AugmentBridgeConfig = field(default_factory=AugmentBridgeConfig)
    security: AugmentSecurityConfig = field(default_factory=AugmentSecurityConfig)

def load_augment_config(raw: dict[str, Any] | None = None) -> AugmentConfig:
    if not raw:
        return AugmentConfig()
    aug_raw = raw.get("augment", raw) if "augment" in raw else raw
    cfg = AugmentConfig(
        enabled=bool(aug_raw.get("enabled", False)),
        mode=str(aug_raw.get("mode", "disabled")),
    )
    if "mcp" in aug_raw:
        m = aug_raw["mcp"]
        cfg.mcp = AugmentMcpConfig(enabled=bool(m.get("enabled", False)))
    if "auggie_cli" in aug_raw:
        c = aug_raw["auggie_cli"]
        cfg.auggie_cli = AugmentCliConfig(
            enabled=bool(c.get("enabled", False)),
            allow_write=bool(c.get("allow_write", False)),
        )
    if "interactive_bridge" in aug_raw:
        b = aug_raw["interactive_bridge"]
        cfg.interactive_bridge = AugmentBridgeConfig(
            enabled=bool(b.get("enabled", False)),
            approval_required_for_write=bool(b.get("approval_required_for_write", True)),
        )
    if "security" in aug_raw:
        s = aug_raw["security"]
        cfg.security = AugmentSecurityConfig(
            workspace_mode=str(s.get("workspace_mode", "task_scoped_copy")),
            send_secrets=bool(s.get("send_secrets", False)),
            require_explicit_project_approval=bool(s.get("require_explicit_project_approval", True)),
        )
    return cfg

def validate_augment_config(config: AugmentConfig) -> list[str]:
    issues = []
    if config.auggie_cli.allow_write and config.security.workspace_mode != "task_scoped_copy":
        issues.append("allow_write=True requires workspace_mode=task_scoped_copy")
    if config.security.send_secrets:
        issues.append("send_secrets=True is not allowed")
    if config.interactive_bridge.enabled and not config.interactive_bridge.approval_required_for_write:
        issues.append("interactive_bridge requires approval_required_for_write=True")
    if config.enabled and not config.security.require_explicit_project_approval:
        issues.append("WARNING: explicit project approval not required")
    return issues
