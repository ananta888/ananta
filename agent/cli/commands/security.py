"""Non-interactive security review commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agent.cli.api_client import AnantaApiClient
from agent.composite_risk_review_contract import COMPOSITE_RISK_REVIEW_WARNING

SUBCOMMANDS = ["composite-risk-review"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ananta security")
    sub = parser.add_subparsers(dest="action", required=True)
    review = sub.add_parser("composite-risk-review")
    review.add_argument("--input", required=True, type=Path, help="JSON review payload")
    review.add_argument("--json", action="store_true", help="Print only machine-readable JSON")
    return parser


def _load_payload(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"composite_risk_review_input_invalid:{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("composite_risk_review_input_must_be_object")
    return {**value, "explicit_request": True}


def dispatch(argv: Sequence[str]) -> int:
    args = _parser().parse_args(list(argv))
    try:
        payload = _load_payload(args.input)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = AnantaApiClient().post(
        "/api/security/composite-risk-review",
        json=payload,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        data = result.get("data") if isinstance(result, dict) else None
        warning = data.get("warning_text") if isinstance(data, dict) else None
        print(warning or COMPOSITE_RISK_REVIEW_WARNING)
        print("Hinweis: Dieses Ergebnis ist keine Sicherheitsfreigabe.")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if isinstance(result, dict) and result.get("status") == "error" else 0


def register(subparsers) -> None:
    parser = subparsers.add_parser("security")
    parser.set_defaults(_dispatch=dispatch)
