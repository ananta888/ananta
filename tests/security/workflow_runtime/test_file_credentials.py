from __future__ import annotations

import os
from pathlib import Path

import pytest

from ananta_contracts import file_credentials as credentials_module
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)


def _credential(path: Path, *, mode: int = 0o600) -> Path:
    path.write_text("credential-value-0123456789abcdef\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_secure_file_reader_turns_nul_path_into_bounded_configuration_error() -> None:
    with pytest.raises(
        FileCredentialConfigurationError,
        match="cannot be opened securely",
    ):
        read_file_managed_bytes(
            "/tmp/workflow\x00credential",
            description="workflow credential file",
            max_bytes=1024,
        )


def test_secure_file_reader_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    target = _credential(tmp_path / "target")
    symlink = tmp_path / "symlink"
    hardlink = tmp_path / "hardlink"
    symlink.symlink_to(target)

    with pytest.raises(FileCredentialConfigurationError, match="opened securely"):
        read_file_managed_bytes(
            str(symlink),
            description="workflow credential file",
            max_bytes=1024,
        )

    hardlink.hardlink_to(target)
    with pytest.raises(FileCredentialConfigurationError, match="link count is unsafe"):
        read_file_managed_bytes(
            str(target),
            description="workflow credential file",
            max_bytes=1024,
        )


def test_secure_file_reader_rejects_group_writable_source(tmp_path: Path) -> None:
    path = _credential(tmp_path / "credential", mode=0o620)

    with pytest.raises(FileCredentialConfigurationError, match="permissions are unsafe"):
        read_file_managed_bytes(
            str(path),
            description="workflow credential file",
            max_bytes=1024,
        )


def test_secure_file_reader_rejects_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _credential(tmp_path / "credential")
    original_fstat = os.fstat

    def foreign_owner_fstat(descriptor: int) -> os.stat_result:
        values = list(original_fstat(descriptor))
        values[4] = max(1, os.geteuid() + 1)
        return os.stat_result(values)

    monkeypatch.setattr(credentials_module.os, "fstat", foreign_owner_fstat)

    with pytest.raises(FileCredentialConfigurationError, match="owner is unsafe"):
        read_file_managed_bytes(
            str(path),
            description="workflow credential file",
            max_bytes=1024,
        )


def test_secure_file_reader_fails_closed_without_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _credential(tmp_path / "credential")
    monkeypatch.delattr(credentials_module.os, "O_NOFOLLOW", raising=False)

    with pytest.raises(FileCredentialConfigurationError, match="secure open is unsupported"):
        read_file_managed_bytes(
            str(path),
            description="workflow credential file",
            max_bytes=1024,
        )
