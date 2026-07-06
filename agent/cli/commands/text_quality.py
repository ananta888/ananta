from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from agent.cli.api_client import AnantaApiClient

SUBCOMMANDS = [
    "evaluate",
    "criteria-extract",
    "criteria-list",
    "criteria-activate",
    "criteria-reject",
    "criteria-archive",
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ananta text-quality")
    sub = parser.add_subparsers(dest="action", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("text")
    evaluate.add_argument("--language", default="de")
    evaluate.add_argument("--content-kind", default="freeform_prose")
    extract = sub.add_parser("criteria-extract")
    extract.add_argument("examples", nargs="+")
    extract.add_argument("--language", default="de")
    extract.add_argument("--content-kind", default="freeform_prose")
    extract.add_argument("--comments", default="")
    sub.add_parser("criteria-list")
    for action in ("criteria-activate", "criteria-reject", "criteria-archive"):
        command = sub.add_parser(action)
        command.add_argument("criteria_id")
    return parser


def dispatch(argv: Sequence[str]) -> int:
    args = _parser().parse_args(list(argv))
    client = AnantaApiClient()
    if args.action == "evaluate":
        result = client.post(
            "/api/text-quality/evaluate",
            json={
                "text": args.text,
                "language": args.language,
                "content_kind": args.content_kind,
            },
        )
    elif args.action == "criteria-extract":
        result = client.post(
            "/api/text-quality/criteria/extract",
            json={
                "examples": args.examples,
                "language": args.language,
                "content_kind": args.content_kind,
                "comments": args.comments,
            },
        )
    elif args.action == "criteria-list":
        result = client.get("/api/text-quality/criteria")
    else:
        suffix = args.action.removeprefix("criteria-")
        result = client.post(f"/api/text-quality/criteria/{args.criteria_id}/{suffix}", json={})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def register(subparsers) -> None:
    parser = subparsers.add_parser("text-quality")
    parser.set_defaults(_dispatch=dispatch)
