#!/usr/bin/env python3
"""Non-interactive business-controlling runtime switch CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.services.business_controlling_runtime_control import (
    BusinessControllingRuntimeControlService,
    JsonBusinessControllingRuntimeControlRepository,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path("data/business-controlling-runtime-control.json"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show")
    replace = subparsers.add_parser("replace")
    replace.add_argument("--expected-revision", required=True, type=int)
    replace.add_argument("--global-enabled", required=True, choices=("true", "false"))
    replace.add_argument("--statistical-enabled", required=True, choices=("true", "false"))
    replace.add_argument("--explanations-enabled", required=True, choices=("true", "false"))
    replace.add_argument("--disabled-entry", action="append", default=[])
    replace.add_argument("--actor-id", required=True)
    replace.add_argument("--reason", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    repository = JsonBusinessControllingRuntimeControlRepository(args.state_path)
    if args.command == "show":
        state = repository.snapshot()
    else:
        state = BusinessControllingRuntimeControlService(repository).replace(
            expected_revision=args.expected_revision,
            global_enabled=args.global_enabled == "true",
            statistical_enabled=args.statistical_enabled == "true",
            explanations_enabled=args.explanations_enabled == "true",
            disabled_catalog_entry_ids=tuple(args.disabled_entry),
            actor_id=args.actor_id,
            reason=args.reason,
        )
    print(json.dumps(state.to_mapping(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
