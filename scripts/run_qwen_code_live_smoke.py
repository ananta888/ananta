#!/usr/bin/env python3
"""Bounded, opt-in live smoke for the Qwen Code headless adapter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from agent.cli_backends.coding_agent_contract import CodingAgentRunRequest, ProviderState
from agent.cli_backends.coding_agent_profiles import CLI_PROFILES, build_cli_coding_agent_provider

_OPT_IN_ENVIRONMENT = "ANANTA_QWEN_LIVE_SMOKE"
_MODEL_ENVIRONMENT = "ANANTA_QWEN_LIVE_SMOKE_MODEL"
_TIMEOUT_ENVIRONMENT = "ANANTA_QWEN_LIVE_SMOKE_TIMEOUT_SECONDS"
_RESULT_FILE = "qwen-smoke-result.txt"
_RESULT_CONTENT = "ananta-qwen-headless-smoke-ok\n"


def run_smoke(
    environment: Mapping[str, str],
    *,
    provider_factory: Callable[[], object] = lambda: build_cli_coding_agent_provider("qwen_code"),
) -> tuple[int, dict[str, object]]:
    if str(environment.get(_OPT_IN_ENVIRONMENT) or "").strip().lower() not in {"1", "true", "yes"}:
        return 0, _payload("skipped", "live_smoke_not_authorized")
    auth_names = CLI_PROFILES["qwen_code"].auth_environment
    if not any(str(environment.get(name) or "").strip() for name in auth_names):
        return 2, _payload("failed", "qwen_live_auth_not_configured")

    provider = provider_factory()
    probe = provider.detect()
    if probe.state is not ProviderState.READY:
        return 2, _payload("failed", probe.reason_code, provider_state=probe.state.value)

    timeout = _bounded_timeout(environment.get(_TIMEOUT_ENVIRONMENT))
    events = []
    with tempfile.TemporaryDirectory(prefix="ananta-qwen-live-smoke-") as temporary_directory:
        workspace = Path(temporary_directory).resolve()
        git_binary = shutil.which("git")
        if not git_binary:
            return 2, _payload("failed", "git_binary_not_installed")
        repository_init = subprocess.run(  # noqa: S603 - absolute executable resolved by shutil.which
            (git_binary, "init", "--quiet"),
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if repository_init.returncode != 0:
            return 2, _payload("failed", "isolated_repository_init_failed")
        (workspace / "README.md").write_text("# Isolated Qwen Code live smoke\n", encoding="utf-8")
        request = CodingAgentRunRequest(
            prompt=(
                f"Create { _RESULT_FILE } in the current repository with exactly this single line: "
                "ananta-qwen-headless-smoke-ok. Do not modify any other file."
            ),
            workspace=workspace,
            timeout_seconds=timeout,
            model=str(environment.get(_MODEL_ENVIRONMENT) or "").strip() or None,
            permission_mode="workspace_write",
            maximum_output_chars=200_000,
        )
        result = provider.run(request, event_sink=events.append)
        result_path = workspace / _RESULT_FILE
        artifact_valid = result_path.is_file() and result_path.read_text(encoding="utf-8") == _RESULT_CONTENT

    if not result.succeeded:
        return 1, _payload(
            "failed",
            result.reason_code,
            return_code=result.return_code,
            duration_ms=result.duration_ms,
            event_count=len(events),
        )
    if not artifact_valid:
        return 1, _payload(
            "failed",
            "qwen_live_artifact_mismatch",
            return_code=result.return_code,
            duration_ms=result.duration_ms,
            event_count=len(events),
        )
    return 0, _payload(
        "passed",
        "qwen_live_smoke_passed",
        return_code=result.return_code,
        duration_ms=result.duration_ms,
        event_count=len(events),
    )


def _bounded_timeout(raw_value: str | None) -> int:
    try:
        parsed = int(str(raw_value or "120").strip())
    except ValueError:
        parsed = 120
    return max(30, min(parsed, 600))


def _payload(status: str, reason_code: str, **details: object) -> dict[str, object]:
    return {
        "schema": "ananta.qwen_code.live_smoke.v1",
        "status": status,
        "reason_code": reason_code,
        "interactive_input_required": False,
        **details,
    }


def main() -> int:
    return_code, payload = run_smoke(os.environ)
    print(json.dumps(payload, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
