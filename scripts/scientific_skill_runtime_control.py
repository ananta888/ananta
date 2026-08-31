#!/usr/bin/env python3
"""Operate scientific-skill kill switches without changing deployed code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.services.scientific_skill_runtime_control_service import (
    JsonScientificSkillRuntimeControlRepository,
    ScientificSkillRuntimeControlError,
    ScientificSkillRuntimeControlService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("data/scientific-skills-runtime-control.json"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show")
    for command in ("global-enable", "global-disable", "entry-enable", "entry-disable"):
        action = subparsers.add_parser(command)
        action.add_argument("--expected-revision", required=True, type=int)
        action.add_argument("--actor", required=True)
        action.add_argument("--reason", required=True)
        if command.startswith("entry-"):
            action.add_argument("--entry-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = JsonScientificSkillRuntimeControlRepository(args.state)
    service = ScientificSkillRuntimeControlService(repository)
    try:
        if args.command == "show":
            state = repository.snapshot()
        elif args.command.startswith("global-"):
            state = service.set_global(
                enabled=args.command == "global-enable",
                expected_revision=args.expected_revision,
                actor_id=args.actor,
                reason=args.reason,
            )
        else:
            state = service.set_entry(
                entry_id=args.entry_id,
                enabled=args.command == "entry-enable",
                expected_revision=args.expected_revision,
                actor_id=args.actor,
                reason=args.reason,
            )
    except ScientificSkillRuntimeControlError as exc:
        print(json.dumps({"ok": False, "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "state": state.to_mapping()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
