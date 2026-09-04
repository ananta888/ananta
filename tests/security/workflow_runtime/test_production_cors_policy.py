from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.config import Settings

ROOT = Path(__file__).resolve().parents[3]


def _subprocess_environment(**overrides: str) -> dict[str, str]:
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(ROOT)
    )
    environment.update(overrides)
    return environment


def _strict_settings(cors_origins: str) -> Settings:
    return Settings(
        cors_origins=cors_origins,
        workflow_require_registered_worker_auth=True,
        _env_file=None,
    )


@pytest.mark.parametrize("cors_origins", ["", "*", "https://console.example.test,*"])
def test_strict_workflow_runtime_rejects_wildcard_or_empty_cors(
    cors_origins: str,
) -> None:
    with pytest.raises(ValidationError, match="explicit origin allowlist"):
        _strict_settings(cors_origins)


@pytest.mark.parametrize(
    "cors_origins",
    [
        "ftp://console.example.test",
        "https://user@console.example.test",
        "https://console.example.test/path",
        "https://console.example.test?tenant=one",
    ],
)
def test_strict_workflow_runtime_rejects_malformed_cors_origins(
    cors_origins: str,
) -> None:
    with pytest.raises(ValidationError, match=r"complete http\(s\) origins"):
        _strict_settings(cors_origins)


def test_strict_workflow_runtime_accepts_explicit_http_origins() -> None:
    settings = _strict_settings(
        "https://console.example.test,http://localhost:4200"
    )

    assert settings.cors_origins == (
        "https://console.example.test,http://localhost:4200"
    )


def test_development_mode_retains_backwards_compatible_wildcard_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The suite may run inside a shell or worker that has a production CORS
    # allowlist configured. This test owns the complete environment boundary
    # for the two settings whose defaults it exercises.
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH", raising=False)
    settings = Settings(_env_file=None)

    assert settings.cors_origins == "*"


def test_real_application_import_fails_closed_for_strict_wildcard_cors(
    tmp_path,
) -> None:
    environment = _subprocess_environment(
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH="1",
        CORS_ORIGINS="*",
        SECRET_KEY="strict-cors-import-test-key-0123456789",
    )

    completed = subprocess.run(
        [sys.executable, "-c", "import agent.config"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode != 0
    assert "explicit origin allowlist" in completed.stderr
    assert "model_construct" not in completed.stderr


def test_real_application_import_fails_closed_for_broken_settings_source(
    tmp_path,
) -> None:
    (tmp_path / "config.json").write_text("{broken-json", encoding="utf-8")
    environment = _subprocess_environment(
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH="1",
        CORS_ORIGINS="https://console.example.test",
        SECRET_KEY="strict-source-import-test-key-0123456789",
    )

    completed = subprocess.run(
        [sys.executable, "-c", "import agent.config"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode != 0
    assert "config.json" in completed.stderr
    assert "model_construct" not in completed.stderr
