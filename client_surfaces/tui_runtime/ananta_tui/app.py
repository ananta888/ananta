from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile
from client_surfaces.tui_runtime.ananta_tui.fixture_transport import build_fixture_transport
from client_surfaces.tui_runtime.ananta_tui.views import (
    render_approval_repair_view,
    render_health_view,
    render_task_artifact_view,
)


class TuiRuntimeApp:
    def __init__(self, client: AnantaApiClient) -> None:
        self._client = client

    def run_once(self) -> str:
        health = self._client.get_health()
        capabilities = self._client.get_capabilities()
        tasks = self._client.list_tasks()
        artifacts = self._client.list_artifacts()
        approvals = self._client.list_approvals()
        repairs = self._client.list_repairs()

        return "\n\n".join(
            [
                render_health_view(self._client.profile, health, capabilities),
                render_task_artifact_view(tasks, artifacts),
                render_approval_repair_view(approvals, repairs),
            ]
        )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Ananta TUI runtime MVP.")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--profile-id", default="default")
    parser.add_argument("--auth-mode", default="session_token")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--environment", default="local")
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON summary instead of text.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        profile = build_client_profile(
            {
                "profile_id": args.profile_id,
                "base_url": args.base_url,
                "auth_mode": args.auth_mode,
                "auth_token": args.auth_token,
                "environment": args.environment,
                "timeout_seconds": args.timeout_seconds,
            }
        )
    except ValueError as exc:
        print(f"[TUI-ERROR] invalid_profile: {exc}")
        return 2

    transport = build_fixture_transport() if args.fixture else None
    client = AnantaApiClient(profile, transport=transport)
    output = TuiRuntimeApp(client).run_once()
    if args.json:
        print(json.dumps({"schema": "ananta_tui_runtime_output_v1", "output": output}, ensure_ascii=False))
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
