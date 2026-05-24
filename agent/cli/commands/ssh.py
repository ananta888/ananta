"""ananta ssh — OIDC-backed SSH certificate login and terminal access."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta ssh",
        description="OIDC-backed SSH certificate login and native terminal access.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Commands:\n"
            "  ananta ssh login\n"
            "      Start OIDC Authorization Code Flow with PKCE, receive a short-lived SSH certificate.\n\n"
            "  ananta ssh targets\n"
            "      List terminal-capable targets the current OIDC identity is allowed to access.\n\n"
            "  ananta ssh connect --target-type worker --target-id <id>\n"
            "      Connect to a worker terminal using the cached SSH certificate.\n\n"
            "  ananta ssh connect --target-type hub --reason <reason>\n"
            "      Connect to a hub terminal (requires explicit permission + reason).\n\n"
            "Note: native SSH requires NATIVE_SSH_ENABLED=true and a configured SSH CA backend.\n"
            "      The issued certificate is short-lived and bound to a single target type.\n"
            "      OIDC tokens and private certificate material are never printed.\n"
        ),
    )
    sub = p.add_subparsers(dest="ssh_cmd", metavar="<action>")

    sub.add_parser("login", help="Start OIDC login and receive a short-lived SSH certificate.")

    sub.add_parser("targets", help="List SSH-accessible terminal targets for the current identity.")

    connect_p = sub.add_parser("connect", help="Connect to a terminal target via SSH certificate.")
    connect_p.add_argument(
        "--target-type",
        choices=["worker", "hub", "hub_as_worker"],
        default="worker",
        help="Target type. Hub and hub_as_worker require explicit permission.",
    )
    connect_p.add_argument("--target-id", default="", help="Target ID. Required when multiple workers exist.")
    connect_p.add_argument("--workspace", default=None, help="Workspace path (must be within allowed base).")
    connect_p.add_argument("--goal-id", default=None)
    connect_p.add_argument("--task-id", default=None)
    connect_p.add_argument(
        "--reason",
        default="",
        help="Required when --target-type=hub or --target-type=hub_as_worker.",
    )

    return p


def _cmd_login(args: argparse.Namespace, base_url: str, token: str | None) -> int:
    from agent.cli.api_client import AnantaApiClient

    client = AnantaApiClient(base_url=base_url, token=token)
    try:
        resp = client.get("/auth/oidc/login", allow_redirects=False)
    except Exception as exc:
        print(f"[ananta ssh] login request failed: {exc}", file=sys.stderr)
        return 1

    # The login endpoint redirects to the OIDC provider
    location = resp.headers.get("Location", "")
    if not location:
        print("[ananta ssh] login: no redirect location returned by server.", file=sys.stderr)
        return 1

    print(f"[ananta ssh] Open this URL in your browser to complete OIDC login:\n\n  {location}\n")
    print("After authentication, the server will issue a short-lived SSH certificate.")
    print("Certificate material is stored server-side and never printed here.")
    return 0


def _cmd_targets(args: argparse.Namespace, base_url: str, token: str | None) -> int:
    from agent.cli.api_client import AnantaApiClient

    client = AnantaApiClient(base_url=base_url, token=token)
    try:
        resp = client.get("/terminal/targets")
        data = resp.json()
    except Exception as exc:
        print(f"[ananta ssh] targets request failed: {exc}", file=sys.stderr)
        return 1

    targets = data.get("targets") or []
    if not targets:
        print("[ananta ssh] No SSH-accessible terminal targets available.")
        return 0

    print(f"{'TARGET TYPE':<20} {'TARGET ID':<36} {'RISK':<10} {'CREATE':<8}")
    print("-" * 80)
    for t in targets:
        risk = "HIGH RISK" if t.get("target_type") in ("hub", "hub_as_worker") else ""
        cap = t.get("capabilities") or {}
        can_create = "yes" if cap.get("create") else "no"
        print(f"{t.get('target_type', ''):<20} {t.get('target_id', ''):<36} {risk:<10} {can_create:<8}")
    return 0


def _cmd_connect(args: argparse.Namespace, base_url: str, token: str | None) -> int:
    from agent.cli.api_client import AnantaApiClient

    target_type = args.target_type
    target_id = args.target_id
    reason = args.reason.strip()

    if target_type in ("hub", "hub_as_worker"):
        if not reason:
            print(
                f"[ananta ssh] --reason is required for --target-type={target_type}.\n"
                "Hub terminal access is HIGH RISK and requires an explicit reason.",
                file=sys.stderr,
            )
            return 1
        print(
            f"\n  *** HIGH RISK: {target_type.upper()} terminal access ***\n"
            f"  Reason: {reason}\n"
            "  This gives direct access to the orchestration runtime.\n"
            "  Proceeding only if authorized by policy.\n",
        )

    client = AnantaApiClient(base_url=base_url, token=token)
    try:
        resp = client.post("/terminal/sessions", {
            "target_type": target_type,
            "target_id": target_id or target_type,
            "workspace_path": args.workspace,
            "goal_id": args.goal_id,
            "task_id": args.task_id,
        })
        data = resp.json()
    except Exception as exc:
        print(f"[ananta ssh] session create failed: {exc}", file=sys.stderr)
        return 1

    session = (data.get("data") or {}).get("session") or {}
    session_id = session.get("id")
    if not session_id:
        reason_code = (data.get("data") or {}).get("reason_code", "unknown")
        print(f"[ananta ssh] session create denied: {reason_code}", file=sys.stderr)
        return 1

    # Obtain attach token and print the SSH connection hint
    try:
        tok_resp = client.post(f"/terminal/sessions/{session_id}/attach-token", {})
        tok_data = tok_resp.json()
        attach_token = (tok_data.get("data") or {}).get("attach_token")
    except Exception as exc:
        print(f"[ananta ssh] attach-token request failed: {exc}", file=sys.stderr)
        return 1

    if not attach_token:
        print("[ananta ssh] attach-token not returned.", file=sys.stderr)
        return 1

    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    host = parsed.hostname or "ananta-hub"
    user = f"ananta-{target_type}"

    print(f"\n[ananta ssh] Session {session_id[:8]}… created.")
    print(f"\nConnect using:\n")
    print(f"  ssh -i ~/.ssh/ananta_ssh_key -o CertificateFile=~/.ssh/ananta_ssh_cert.pub \\")
    print(f"      {user}@{host}")
    print(f"\nOr use the WebSocket terminal:\n")
    print(f"  wss://{parsed.netloc}/ws/terminal/session?attach_token=<redacted>")
    print(f"\nAttach token: (not printed — use the web UI or the ananta tmux attach command)\n")
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    base_url = os.environ.get("ANANTA_BASE_URL", "http://localhost:5000")
    token = os.environ.get("ANANTA_TOKEN") or os.environ.get("ANANTA_USER_TOKEN")

    if not args.ssh_cmd:
        parser.print_help()
        return 0

    if args.ssh_cmd == "login":
        return _cmd_login(args, base_url, token)
    if args.ssh_cmd == "targets":
        return _cmd_targets(args, base_url, token)
    if args.ssh_cmd == "connect":
        return _cmd_connect(args, base_url, token)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(run())
