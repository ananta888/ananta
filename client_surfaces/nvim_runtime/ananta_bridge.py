from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.context_packaging import package_editor_context
from client_surfaces.common.profile_auth import build_client_profile
from client_surfaces.common.types import ClientResponse

_DEFAULT_GOAL_TEXT = "Goal from Neovim"


def _fixture_response(command: str) -> ClientResponse:
    fixture_data: dict[str, dict[str, Any]] = {
        "goal_submit": {
            "goal_id": "goal-nvim-fixture",
            "task_id": "task-nvim-goal-1",
            "browser_url": "http://localhost:8080/goals/goal-nvim-fixture",
        },
        "analyze": {"task_id": "task-nvim-analyze-1", "status": "queued", "summary": "Analyze accepted"},
        "review": {"task_id": "task-nvim-review-1", "status": "queued", "summary": "Review accepted"},
        "patch_plan": {"task_id": "task-nvim-patch-1", "status": "queued", "summary": "Patch planning accepted"},
        "project_new": {
            "task_id": "task-nvim-project-new-1",
            "status": "queued",
            "summary": "Project creation accepted",
        },
        "project_evolve": {
            "task_id": "task-nvim-project-evolve-1",
            "status": "queued",
            "summary": "Project evolution accepted",
        },
    }
    return ClientResponse(
        ok=True,
        status_code=200,
        state="healthy",
        data=fixture_data.get(command, {"status": "ok"}),
        error=None,
        retriable=False,
    )


def _dispatch_command(
    *,
    command: str,
    goal_text: str,
    context_payload: dict[str, Any],
    client: AnantaApiClient,
) -> ClientResponse:
    if os.getenv("ANANTA_NVIM_FIXTURE") == "1":
        return _fixture_response(command)
    if command == "goal_submit":
        return client.submit_goal(goal_text=goal_text, context_payload=context_payload)
    if command == "analyze":
        return client.analyze_context(context_payload=context_payload)
    if command == "review":
        return client.review_context(context_payload=context_payload)
    if command == "patch_plan":
        return client.patch_plan(context_payload=context_payload)
    if command == "project_new":
        return client.create_project_new(goal_text=goal_text, context_payload=context_payload)
    if command == "project_evolve":
        return client.create_project_evolve(goal_text=goal_text, context_payload=context_payload)
    raise ValueError(f"unsupported command: {command}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Neovim runtime bridge to Ananta API client layer.")
    parser.add_argument(
        "--command",
        required=True,
        choices=[
            "goal_submit",
            "analyze",
            "review",
            "patch_plan",
            "project_new",
            "project_evolve",
        ],
    )
    parser.add_argument("--goal-text", default=_DEFAULT_GOAL_TEXT)
    parser.add_argument("--profile-id", default="nvim-default")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--auth-mode", default="session_token")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--environment", default="local")
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--file-path", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--selection-text", default="")
    parser.add_argument("--max-selection-chars", type=int, default=2000)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
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
    context_payload = package_editor_context(
        file_path=args.file_path or None,
        project_root=args.project_root or None,
        selection_text=args.selection_text or None,
        max_selection_chars=max(1, int(args.max_selection_chars)),
    )
    response = _dispatch_command(
        command=args.command,
        goal_text=str(args.goal_text or _DEFAULT_GOAL_TEXT),
        context_payload=context_payload,
        client=AnantaApiClient(profile),
    )
    out = {
        "schema": "ananta_nvim_bridge_response_v1",
        "command": args.command,
        "context": context_payload,
        "response": {
            "ok": response.ok,
            "status_code": response.status_code,
            "state": response.state,
            "data": response.data,
            "error": response.error,
            "retriable": response.retriable,
        },
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if response.ok else 2


if __name__ == "__main__":
    sys.exit(main())
