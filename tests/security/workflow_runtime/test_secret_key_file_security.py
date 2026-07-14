from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.config import (
    SecretKeyConfigurationError,
    _resolve_file_managed_secret_key,
)

ROOT = Path(__file__).resolve().parents[3]


def _secret_file(path: Path) -> Path:
    path.write_text("s" * 48, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_file_managed_secret_key_rejects_nul_path_as_configuration_error() -> None:
    with pytest.raises(SecretKeyConfigurationError, match="SECRET_KEY file is invalid"):
        _resolve_file_managed_secret_key(
            inline_secret_key="",
            secret_key_file="/tmp/secret\x00key",
        )


def test_file_managed_secret_key_rejects_conflicting_inline_value(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "session-signing-key")

    with pytest.raises(SecretKeyConfigurationError, match="conflicts"):
        _resolve_file_managed_secret_key(
            inline_secret_key="different-inline-secret-key-value-000000",
            secret_key_file=str(path),
        )


def test_config_import_fails_closed_for_unsafe_explicit_secret_key_file(
    tmp_path: Path,
) -> None:
    target = _secret_file(tmp_path / "target-session-signing-key")
    link = tmp_path / "linked-session-signing-key"
    link.symlink_to(target)
    environment = dict(os.environ)
    environment.update(
        {
            "SECRET_KEY": "",
            "SECRET_KEY_FILE": str(link),
            "SECRETS_DIR": str(tmp_path / "legacy-secrets"),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", "import agent.config"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode != 0
    assert "File-managed SECRET_KEY configuration is invalid" in completed.stderr
    assert "s" * 48 not in completed.stderr


def test_config_import_accepts_safe_explicit_secret_key_file(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "session-signing-key")
    environment = dict(os.environ)
    environment.update(
        {
            "SECRET_KEY": "",
            "SECRET_KEY_FILE": str(path),
            "SECRETS_DIR": str(tmp_path / "legacy-secrets"),
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from agent.config import settings; "
                "assert settings.secret_key_file; "
                "assert len(settings.secret_key.encode('utf-8')) >= 32"
            ),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert "s" * 48 not in completed.stdout
    assert "s" * 48 not in completed.stderr
