from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from agent.cli_goals import get_auth_token, get_base_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta voice-file",
        description="Send a local audio file to Ananta Hub voice endpoints.",
    )
    parser.add_argument("path", help="Path to local audio file (wav/mp3/m4a/webm/ogg)")
    parser.add_argument("--mode", choices=("transcribe", "command", "goal"), default="transcribe")
    parser.add_argument("--language", default=None)
    parser.add_argument("--create-tasks", action="store_true", default=False)
    parser.add_argument("--approved", action="store_true", default=False)
    return parser


def _endpoint_for_mode(mode: str) -> str:
    if mode == "transcribe":
        return "/v1/voice/transcribe"
    if mode == "command":
        return "/v1/voice/command"
    return "/v1/voice/goal"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    file_path = Path(args.path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        print(f"Error: file not found: {file_path}")
        return 2

    base_url = get_base_url()
    token = get_auth_token(base_url)
    endpoint = _endpoint_for_mode(args.mode)
    url = f"{base_url}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}

    data: dict[str, str] = {}
    if args.language:
        data["language"] = str(args.language)
    if args.mode == "goal":
        data["create_tasks"] = "true" if bool(args.create_tasks) else "false"
        data["approved"] = "true" if bool(args.approved) else "false"

    with file_path.open("rb") as handle:
        files = {"file": (file_path.name, handle, "application/octet-stream")}
        response = requests.post(url, headers=headers, files=files, data=data, timeout=120)

    try:
        payload = response.json()
    except ValueError:
        payload = {"status": "error", "message": response.text}

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if response.status_code < 400 else 1
