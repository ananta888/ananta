from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent import auth as auth_module
from agent.auth import AgentTokenConfigurationError, resolve_configured_agent_token

SERVICE_TOKEN_A = "workflow-hub-service-token-a-0123456789abcdef"
SERVICE_TOKEN_B = "workflow-hub-service-token-b-0123456789abcdef"


def _write_secret(path: Path, value: str, *, mode: int = 0o600) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(mode)


def _resolve(path: Path) -> str | None:
    return resolve_configured_agent_token(
        {
            "AGENT_TOKEN": None,
            "AGENT_TOKEN_FILE": str(path),
        }
    )


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="platform has no O_NOFOLLOW")
def test_agent_token_file_rejects_symlink_without_disclosing_secret(tmp_path: Path) -> None:
    target = tmp_path / "actual-token"
    symlink = tmp_path / "configured-token"
    _write_secret(target, SERVICE_TOKEN_A)
    symlink.symlink_to(target)

    with pytest.raises(AgentTokenConfigurationError) as raised:
        _resolve(symlink)

    assert "opened securely" in str(raised.value)
    assert SERVICE_TOKEN_A not in str(raised.value)


def test_agent_token_file_path_swap_after_open_fails_closed_on_unlinked_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured-token"
    replacement = tmp_path / "replacement-token"
    _write_secret(configured, SERVICE_TOKEN_A)
    _write_secret(replacement, SERVICE_TOKEN_B)
    original_open = os.open

    def open_then_replace(path: os.PathLike[str], flags: int) -> int:
        descriptor = original_open(path, flags)
        os.replace(replacement, configured)
        return descriptor

    monkeypatch.setattr(auth_module.os, "open", open_then_replace)

    with pytest.raises(AgentTokenConfigurationError, match="link count is unsafe"):
        _resolve(configured)


def test_agent_token_file_rejects_nul_path_as_configuration_error() -> None:
    with pytest.raises(AgentTokenConfigurationError, match="opened securely"):
        resolve_configured_agent_token(
            {"AGENT_TOKEN": None, "AGENT_TOKEN_FILE": "/tmp/invalid\x00token"}
        )


def test_agent_token_file_fails_closed_without_no_follow_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured-token"
    _write_secret(configured, SERVICE_TOKEN_A)
    monkeypatch.delattr(auth_module.os, "O_NOFOLLOW", raising=False)

    with pytest.raises(AgentTokenConfigurationError) as raised:
        _resolve(configured)

    assert str(raised.value) == "agent token file secure open is unsupported"
    assert SERVICE_TOKEN_A not in str(raised.value)


def test_agent_token_file_rejects_untrusted_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured-token"
    _write_secret(configured, SERVICE_TOKEN_A)
    original_fstat = os.fstat

    def foreign_owner_fstat(descriptor: int) -> os.stat_result:
        values = list(original_fstat(descriptor))
        values[4] = max(1, os.geteuid() + 1)
        return os.stat_result(values)

    monkeypatch.setattr(auth_module.os, "fstat", foreign_owner_fstat)

    with pytest.raises(AgentTokenConfigurationError, match="owner is unsafe"):
        _resolve(configured)


@pytest.mark.parametrize(
    ("mode", "content", "error"),
    (
        (0o620, SERVICE_TOKEN_A.encode("utf-8"), "permissions are unsafe"),
        (0o600, b"x" * 16_385, "size is invalid"),
    ),
    ids=("group-writable", "oversized"),
)
def test_agent_token_file_rejects_unsafe_mode_and_size(
    tmp_path: Path,
    mode: int,
    content: bytes,
    error: str,
) -> None:
    configured = tmp_path / "configured-token"
    configured.write_bytes(content)
    configured.chmod(mode)

    with pytest.raises(AgentTokenConfigurationError, match=error):
        _resolve(configured)
