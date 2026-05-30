"""ananta share — Rendezvous-Session-Verwaltung über die Hub-API."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["list", "create", "revoke"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta share",
        description="Rendezvous-Sessions des angemeldeten Nutzers verwalten.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Beispiele:\n"
            "  ananta share list\n"
            "  ananta share create \"Debug Session\"\n"
            "  ananta share revoke <session-id>\n\n"
            "Umgebungsvariablen:\n"
            "  ANANTA_BASE_URL    Hub-URL (default: http://localhost:5000)\n"
            "  ANANTA_USER        Login-Nutzername\n"
            "  ANANTA_PASSWORD    Login-Passwort\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="share_cmd", metavar="<action>")

    sub.add_parser("list", help="Eigene aktive Sessions anzeigen")

    create_p = sub.add_parser("create", help="Neue Session erstellen")
    create_p.add_argument("title", nargs="*", help="Session-Titel (Wörter werden zusammengefügt)")

    revoke_p = sub.add_parser("revoke", help="Session widerrufen")
    revoke_p.add_argument("session_id", help="Session-ID")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    if not args.share_cmd:
        parser.print_help()
        return 0

    from agent.cli.api_client import AnantaApiClient
    try:
        client = AnantaApiClient()
    except SystemExit:
        return 1

    if args.share_cmd == "list":
        return _cmd_list(client)
    if args.share_cmd == "create":
        title = " ".join(args.title).strip() or "Shared Session"
        return _cmd_create(client, title)
    if args.share_cmd == "revoke":
        return _cmd_revoke(client, args.session_id)

    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("share", help="Rendezvous-Sessions verwalten.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


def _cmd_list(client: Any) -> int:
    result = client._call("GET", "/rendezvous/sessions")
    sessions = (result.get("data") or {}).get("items") or []
    if not sessions:
        print("Keine aktiven Sessions.")
        return 0
    print(f"{len(sessions)} aktive Session(s):\n")
    for s in sessions:
        sid = str(s.get("id") or "")
        title = str(s.get("title") or "Session")
        pcount = len(s.get("participants") or [])
        invite = str(s.get("invite_code") or "")
        owner = str(s.get("owner_user_id") or "")
        print(f"  {title}")
        print(f"    ID:         {sid}")
        print(f"    Owner:      {owner}")
        print(f"    Invite:     {invite or '(kein Code)'}")
        print(f"    Teilnehmer: {pcount}")
        print()
    return 0


def _cmd_create(client: Any, title: str) -> int:
    import os
    body = {
        "title": title,
        "owner_device_fingerprint": os.environ.get("ANANTA_DEVICE_FINGERPRINT", "cli-device"),
    }
    result = client._call("POST", "/rendezvous/sessions", json=body)
    if not result.get("ok") and not result.get("id"):
        print(f"Fehler: {result.get('error', result)}", file=sys.stderr)
        return 1
    session = result.get("data") or result
    sid = str(session.get("id") or "")
    invite = str(session.get("invite_code") or "")
    print(f"Session erstellt: '{title}'")
    print(f"  ID:     {sid}")
    print(f"  Invite: {invite}")
    return 0


def _cmd_revoke(client: Any, session_id: str) -> int:
    result = client._call("DELETE", f"/rendezvous/sessions/{session_id}")
    if not result.get("ok"):
        print(f"Fehler: {result.get('error', result)}", file=sys.stderr)
        return 1
    print(f"Session {session_id} widerrufen.")
    return 0
